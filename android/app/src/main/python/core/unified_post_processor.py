"""Unified 5-in-1 Chapter Post-Processor (S3P Pass 2).

Consolidates five previously separate post-chapter LLM calls into a single
structured JSON pass:
  1. Chapter bridge (for seamless next-chapter continuity)
  2. Chapter summary & canon key events (for global_state.summaries)
  3. Character state / location / inventory deltas (for global_state.characters)
  4. Foreshadowing updates (planted / hinted / resolved)
  5. Consistency & outline-alignment violations with exact source quotes
     (for targeted surgical patching instead of blind full-chapter rewrites)
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

UNIFIED_POST_SYSTEM = """你是长篇小说连续性总编。请阅读刚定稿的一章正文，一次性输出严格 JSON，同时完成五项任务：
1. bridge：供下一章无缝衔接的桥接信息（summary 80-120字、ending_scene 结尾画面、next_beat 下一章紧接动作、unresolved_tension 未解悬念）；
2. summary_entry：归档摘要（summary 60-100字、key_events 2-4条已发生正史事件数组、characters 本章实际出场角色名数组）；
3. character_updates：仅记录本章位置/状态/情绪/物品发生真实变化的角色（字典：角色名 -> {location, status, emotion, items}）；
4. foreshadow_updates：本章新埋设、再暗示或已回收的伏笔数组（每项 {title, status: planted|hinted|resolved, detail}）；
5. consistency_violations：仅记录确凿的逻辑硬伤或设定矛盾（每项 {severity: "P0"|"P1", quote: "原文原句摘录", description: "矛盾说明", fix_hint: "局部修改建议"}）；没有硬伤则输出空数组 []。

只输出合法 JSON，不要任何解释文字：
{
  "bridge": {"summary": "", "ending_scene": "", "next_beat": "", "unresolved_tension": ""},
  "summary_entry": {"summary": "", "key_events": [], "characters": []},
  "character_updates": {},
  "foreshadow_updates": [],
  "consistency_violations": []
}"""


def _extract_json(raw: str) -> Dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(
            lines[1:-1] if len(lines) >= 2 and lines[-1].strip().startswith("```") else lines[1:]
        ).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        text = text[start:end + 1]
    return json.loads(text)


def _fallback_post_result(chapter_num: int, chapter_text: str, chapter_outline: Optional[dict]) -> Dict[str, Any]:
    clean = (chapter_text or "").strip()
    tail = clean[-180:] if len(clean) > 180 else clean
    outline = chapter_outline or {}
    summary = str(outline.get("summary") or clean[:120]).strip()
    chars = [str(c).strip() for c in (outline.get("characters") or []) if str(c).strip()]
    return {
        "bridge": {
            "chapter": chapter_num,
            "summary": summary,
            "ending_scene": tail,
            "next_beat": str(outline.get("hook") or "承接本章结尾继续推进").strip(),
            "unresolved_tension": str(outline.get("hook") or "").strip(),
            "fallback": True,
        },
        "summary_entry": {
            "chapter": chapter_num,
            "summary": summary,
            "key_events": [summary] if summary else [],
            "characters": chars,
            "fallback": True,
        },
        "character_updates": {},
        "foreshadow_updates": [],
        "consistency_violations": [],
        "fallback": True,
    }


class UnifiedPostProcessor:
    """Single-call post-chapter state extractor and consistency auditor."""

    def __init__(self, resilient_client: Any, model: str):
        self._client = resilient_client
        self.model = model

    def analyze(
        self,
        chapter_num: int,
        chapter_text: str,
        chapter_outline: Optional[dict] = None,
        current_global_state: Optional[dict] = None,
    ) -> Dict[str, Any]:
        clean = (chapter_text or "").strip()
        if len(clean) < 80 or self._client is None:
            return _fallback_post_result(chapter_num, clean, chapter_outline)

        outline = chapter_outline or {}
        gs = current_global_state or {}
        known_chars = list((gs.get("characters") or {}).keys())[:15]
        user_prompt = (
            f"章节：第{chapter_num}章《{outline.get('title', '')}》\n"
            f"本章大纲概要：{outline.get('summary', '无')}\n"
            f"已有角色列表：{', '.join(known_chars) if known_chars else '尚未建立'}\n\n"
            f"【第{chapter_num}章正文】\n{clean[:6000]}"
        )
        try:
            resp = self._client.create(
                messages=[
                    {"role": "system", "content": UNIFIED_POST_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                max_tokens=1600,
                response_format={"type": "json_object"},
            )
            raw = resp.choices[0].message.content or ""
            parsed = _extract_json(raw)
            if not isinstance(parsed, dict):
                raise ValueError("UnifiedPostProcessor expected JSON object")
            bridge = parsed.get("bridge") if isinstance(parsed.get("bridge"), dict) else {}
            bridge.setdefault("chapter", chapter_num)
            bridge.setdefault("ending_scene", clean[-160:])
            summary_entry = parsed.get("summary_entry") if isinstance(parsed.get("summary_entry"), dict) else {}
            summary_entry.setdefault("chapter", chapter_num)
            return {
                "bridge": bridge,
                "summary_entry": summary_entry,
                "character_updates": parsed.get("character_updates") if isinstance(parsed.get("character_updates"), dict) else {},
                "foreshadow_updates": parsed.get("foreshadow_updates") if isinstance(parsed.get("foreshadow_updates"), list) else [],
                "consistency_violations": parsed.get("consistency_violations") if isinstance(parsed.get("consistency_violations"), list) else [],
                "fallback": False,
            }
        except Exception as exc:
            log.warning("UnifiedPostProcessor fell back to deterministic mode for Ch%d: %s", chapter_num, exc)
            return _fallback_post_result(chapter_num, clean, chapter_outline)

    def process_and_persist(
        self,
        memory: Any,
        novel_id: str,
        chapter_num: int,
        chapter_text: str,
        chapter_outline: Optional[dict] = None,
    ) -> Dict[str, Any]:
        """Run unified 5-in-1 extraction and atomically persist into SharedMemoryManager."""
        current_gs = memory.read("global_state", novel_id) if hasattr(memory, "read") else {}
        if not isinstance(current_gs, dict):
            current_gs = {}

        result = self.analyze(
            chapter_num=chapter_num,
            chapter_text=chapter_text,
            chapter_outline=chapter_outline,
            current_global_state=current_gs,
        )

        # 1. Save bridge for next-chapter continuity
        bridge = result.get("bridge")
        if bridge and hasattr(memory, "save_bridge"):
            try:
                memory.save_bridge(novel_id, chapter_num, bridge)
            except Exception as exc:
                log.warning("UnifiedPostProcessor save_bridge skipped: %s", exc)

        # 2. Atomically merge summary, character updates, and foreshadowing into global_state
        def _merge_global_state(gs: Any) -> Dict[str, Any]:
            state = dict(gs) if isinstance(gs, dict) else {}
            # Summary
            summary_entry = result.get("summary_entry")
            if isinstance(summary_entry, dict) and summary_entry.get("summary"):
                state.setdefault("summaries", {})[str(chapter_num)] = summary_entry
            # Characters
            char_updates = result.get("character_updates") or {}
            if isinstance(char_updates, dict) and char_updates:
                chars_map = state.setdefault("characters", {})
                if isinstance(chars_map, dict):
                    for name, delta in char_updates.items():
                        if not isinstance(delta, dict) or not str(name).strip():
                            continue
                        existing = dict(chars_map.get(name, {})) if isinstance(chars_map.get(name), dict) else {}
                        for k, v in delta.items():
                            if v not in (None, "", []):
                                existing[k] = v
                        existing["last_chapter"] = chapter_num
                        chars_map[name] = existing
            # Foreshadowing
            fs_updates = result.get("foreshadow_updates") or []
            if isinstance(fs_updates, list) and fs_updates:
                fs_list = state.setdefault("foreshadowing", [])
                if isinstance(fs_list, list):
                    by_title = {
                        str(item.get("title", "")).strip(): idx
                        for idx, item in enumerate(fs_list)
                        if isinstance(item, dict) and item.get("title")
                    }
                    for item in fs_updates:
                        if not isinstance(item, dict) or not str(item.get("title", "")).strip():
                            continue
                        title = str(item["title"]).strip()
                        if title in by_title:
                            fs_list[by_title[title]].update({
                                "status": item.get("status", fs_list[by_title[title]].get("status", "hinted")),
                                "detail": item.get("detail") or fs_list[by_title[title]].get("detail", ""),
                                "updated_chapter": chapter_num,
                            })
                        else:
                            fs_list.append({
                                "title": title,
                                "status": item.get("status", "planted"),
                                "detail": item.get("detail", ""),
                                "planted_chapter": chapter_num,
                                "updated_chapter": chapter_num,
                            })
            state["last_updated_chapter"] = chapter_num
            return state

        if hasattr(memory, "update"):
            try:
                memory.update("global_state", novel_id, _merge_global_state)
            except Exception as exc:
                log.warning("UnifiedPostProcessor global_state merge skipped: %s", exc)

        return result
