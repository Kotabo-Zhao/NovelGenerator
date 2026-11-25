"""Zero-intervention Micro-Director Card generator.

Before prose drafting starts, produces a compact 2-3 scene acting sheet
(physical sensory anchor, subtext tension, lived-in anti-cliche detail)
so the writer LLM focuses on vivid dramatization instead of flat plot summary.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

log = logging.getLogger(__name__)

_DIRECTOR_SYSTEM = """你是顶级中文小说微观导演。根据本章大纲，为正文写手准备一份极简的「表演提示卡」，只输出合法 JSON，不要多余解释。
原则：
1. 必须具体可感：给出具体的光影/气温/气味/手里碰到的物件，拒绝抽象形容词。
2. 必须有潜台词：写出角色「嘴上说的」与「心里真正在算计/回避的」之间的落差。
3. 必须反套路：加入 1 个打破工整起承转合的真实生活小细节（停顿、打岔、小失误、不合时宜的念头）。"""


def _extract_json_obj(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    if not text:
        return None
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return None
        try:
            parsed = json.loads(m.group(0))
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None


def format_director_card(card: dict[str, Any]) -> str:
    """Format a structured director card dict into a compact writer instruction block."""
    if not isinstance(card, dict):
        return ""
    scenes = card.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        return ""
    lines = [
        "## 🎬 本章表演微观提示（自然融进动作、对话与感官细节，严禁直接复述条目）"
    ]
    for idx, sc in enumerate(scenes[:3], 1):
        if not isinstance(sc, dict):
            continue
        sensory = str(sc.get("sensory_anchor") or "").strip()
        subtext = str(sc.get("subtext") or "").strip()
        detail = str(sc.get("lived_in_detail") or "").strip()
        parts = []
        if sensory:
            parts.append(f"感官锚点：{sensory}")
        if subtext:
            parts.append(f"潜台词张力：{subtext}")
        if detail:
            parts.append(f"生活微细节：{detail}")
        if parts:
            lines.append(f"- 场景{idx}：" + " ｜ ".join(parts))
    return "\n".join(lines) if len(lines) > 1 else ""


def build_director_card(
    resilient_client: Any,
    model: str,
    chapter_outline: dict[str, Any],
    context_snippet: str = "",
    *,
    genre: str = "玄幻",
    style: str = "热血爽文",
    recent_cliches: list[str] | None = None,
) -> str:
    """Synchronously generate a compact director card block (safe to run via asyncio.to_thread)."""
    if resilient_client is None or not isinstance(chapter_outline, dict):
        return ""
    title = str(chapter_outline.get("title") or "").strip()
    summary = str(chapter_outline.get("summary") or "").strip()
    events = chapter_outline.get("key_events") or chapter_outline.get("events") or []
    if isinstance(events, list):
        events_str = "；".join(str(e) for e in events[:5])
    else:
        events_str = str(events)
    if not summary and not events_str:
        return ""

    taboo_note = ""
    if recent_cliches:
        taboo_note = f"\n上两章高频套话（避免沿用）：{'、'.join(recent_cliches[:6])}"

    user_prompt = (
        f"题材：{genre}｜风格：{style}\n"
        f"章节：{title}\n"
        f"大纲概要：{summary[:450]}\n"
        f"核心事件：{events_str[:300]}\n"
        f"前文衔接参考：{(context_snippet or '')[:350]}{taboo_note}\n\n"
        "请输出 JSON（2-3个场景，每项控制在 25 字内）：\n"
        '{"scenes":[{"sensory_anchor":"具体光影/声音/触感/物件","subtext":"表面说法与真实盘算的落差","lived_in_detail":"一个打破套路的真实小动作或停顿"}]}'
    )

    try:
        resp = resilient_client.create(
            messages=[
                {"role": "system", "content": _DIRECTOR_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.75,
            max_tokens=420,
        )
        raw = resp.choices[0].message.content if resp and getattr(resp, "choices", None) else ""
        card = _extract_json_obj(raw or "")
        return format_director_card(card or {})
    except Exception as exc:
        log.warning("SceneDirectorCard skipped (non-fatal): %s", exc)
        return ""
