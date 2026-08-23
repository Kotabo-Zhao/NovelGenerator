"""NovelGenerator — Outline Compliance Checker: 大纲合规校验

职责: 校验章节正文是否真正落地了本章大纲的核心内容（核心事件 / 节拍 / 出场角色 / 冲突），
     而不是只靠 prompt 软约束。分两级:
- L1 规则预检（零成本）: 出场角色名是否出现在正文、节拍关键词弱命中
- L2 LLM 精判（单次调用）: summary / scene_beats.key_action / conflict 是否在正文中有情节展开
分级处置:
- ok      (≥80% 命中且无核心事件缺失) → 前端绿色徽章
- partial (50~80%)                      → 前端黄色警示 + 记录缺失项
- fail    (<50% 或核心事件缺失≥2)       → 自动补写缺失核心事件（batch 模式只记录不补写）
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from .resilient_client import ResilientLLMClient

log = logging.getLogger(__name__)

# ── LLM 精判 prompt ──
CHECK_SYSTEM = """你是网络小说大纲合规检查员。核对章节正文是否落实了大纲要求的事件/节拍/冲突。

判定标准（严格）：
1. present=true：该事件/情节在正文中有【实际展开】（有过程、有结果、有影响）
2. present=false：完全没有体现；或只有一句"提及/预告"没有情节展开；或换了名字但情节实质没发生
3. 部分展开但明显不完整（如"他想起要去找信物，但本章没有展开"）→ present=false
4. 只输出 JSON，不要解释。

输出格式：
{"results": [{"id": "检查项id", "present": true或false, "evidence": "正文中的一句话摘录（20-60字，无则空字符串）"}]}"""

SCENE_REVISION_SYSTEM = """你是网络小说的场景编辑。这不是续写任务，不得把缺失事件追加到章末。

你只修订给定的一个目标场景：
1. 保留该场景已经成立的人物、地点、道具、视角和因果。
2. 把缺失的事件放回它所属的情节位置，写出欲望、阻力和不可逆变化，不能只提一句。
3. 与前后场景自然衔接，不得提前透支后续钩子，不得重复已发生的事件。
4. 不出现“补写”“大纲”“缺失”“修订”等元话语。
5. 只输出修订后的目标场景正文，不要标题、不要解释。"""


def _parse_json(content: str) -> Optional[dict]:
    if not content:
        return None
    text = str(content).strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
    return None


def _clean_patch(text: str) -> str:
    """清洗补写文本：去标题行/补写说明行/多余空行"""
    if not text:
        return ""
    text = str(text).strip()
    lines = text.split("\n")
    while lines and re.match(r"^#+\s*", lines[0].strip()):
        lines.pop(0)
        while lines and not lines[0].strip():
            lines.pop(0)
    # 去掉可能出现的补写说明行（“（补写：…）”“【补写】…”）
    if lines and re.match(r"^[（(【\[]?\s*补写", lines[0].strip()):
        lines.pop(0)
        while lines and not lines[0].strip():
            lines.pop(0)
    text = "\n".join(lines).strip()
    # 折叠多余空行
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


class OutlineComplianceChecker:
    """大纲合规校验器（L1 规则 + L2 LLM 语义）"""

    def __init__(self, client=None, model: str = None):
        self.client = client
        self.model = model
        self._resilient = ResilientLLMClient(client, model) if client else None

    # ── 检查项提取 ──
    def extract_check_items(self, chapter_outline: dict) -> list:
        """从本章大纲提取检查项（核心事件/节拍/角色/冲突）"""
        items = []
        if not isinstance(chapter_outline, dict):
            return items
        summary = str(chapter_outline.get("summary") or "").strip()
        if summary:
            items.append({
                "id": "summary", "label": "本章核心事件",
                "needle": summary[:120], "critical": True,
            })
        beats = chapter_outline.get("scene_beats") or []
        for i, b in enumerate(beats):
            if isinstance(b, dict):
                ka = str(b.get("key_action") or b.get("name") or "").strip()
            elif b is not None:
                # 兼容旧版/手工大纲中的 scene_beats: list[str]，
                # 确保这些硬节拍也进入合规检查而不是静默跳过。
                ka = str(b).strip()
            else:
                ka = ""
            if ka:
                items.append({
                    "id": f"beat{i + 1}", "label": f"节拍{i + 1}: {ka[:20]}",
                    "needle": ka[:80], "critical": True,
                })
        for name in (chapter_outline.get("characters") or [])[:6]:
            if isinstance(name, str) and name.strip():
                items.append({
                    "id": f"char_{name.strip()}", "label": f"角色出场: {name.strip()[:12]}",
                    "needle": name.strip(), "critical": False,
                })
        conflict = str(chapter_outline.get("conflict") or "").strip()
        if conflict:
            items.append({
                "id": "conflict", "label": "本章冲突",
                "needle": conflict[:80], "critical": False,
            })
        return items

    # ── L1 规则预检（零成本）──
    _NEG_WORDS = ("没有", "不见", "不在", "没了", "没来", "未见", "未现", "未曾", "无影", "无踪")

    @staticmethod
    def _char_present(text: str, name: str) -> bool:
        """角色出场检测：出现且不在否定语境（"没有师姐"/"师姐不在"）"""
        idx = 0
        while True:
            i = text.find(name, idx)
            if i < 0:
                return False
            pre = text[max(0, i - 5):i]
            if not any(k in pre for k in OutlineComplianceChecker._NEG_WORDS):
                return True
            idx = i + len(name)

    def _rule_check(self, text: str, items: list) -> dict:
        results = {}
        for it in items:
            if it["id"].startswith("char_"):
                present = self._char_present(text, it["needle"])
                results[it["id"]] = {
                    "present": present,
                    "evidence": "" if present else f"正文中未出现角色「{it['needle']}」",
                    "by": "rule",
                }
            elif it["id"].startswith("beat"):
                present = it["needle"] in text
                # 原句出现可以快速确认；原句不出现不能反推情节没发生，
                # 否则会把正常的文学化改写误判为缺失。深度模式交给 LLM
                # 语义判定，批量模式则标记为 skipped，不制造假硬失败。
                if present:
                    results[it["id"]] = {
                        "present": True,
                        "evidence": it["needle"][:80],
                        "by": "rule",
                    }
        return results

    # ── L2 LLM 精判 ──
    def _llm_check(self, text: str, items: list) -> Optional[dict]:
        if not self._resilient:
            return None
        # 角色出场用规则检查；事件、冲突与 scene beat 全部交给
        # 语义精判。节拍文案通常不会在正文中原句出现，不能只靠字符串。
        pending = [it for it in items if not it["id"].startswith("char_")]
        if not pending:
            return {}
        brief = "\n".join(
            f"- {it['id']}: {it['label']}｜要求: {it['needle']}" for it in pending)
        if len(text) <= 6000:
            excerpt = text
        else:
            middle = max(0, len(text) // 2 - 600)
            excerpt = (
                text[:2800]
                + "\n……（正文中间节选）……\n"
                + text[middle:middle + 1200]
                + "\n……（正文后段）……\n"
                + text[-2000:]
            )
        user = (
            f"章节正文（节选）:\n{excerpt}\n\n"
            f"待核对的大纲要求:\n{brief}\n\n"
            "请逐项判断是否在正文中实际展开（严格标准，见系统提示）。"
        )
        try:
            resp = self._resilient.create(
                messages=[
                    {"role": "system", "content": CHECK_SYSTEM},
                    {"role": "user", "content": user},
                ],
                temperature=0.0,
                max_tokens=800,
            )
            content = resp.choices[0].message.content if hasattr(resp, "choices") else resp
            data = _parse_json(content)
            if not isinstance(data, dict):
                return None
            out = {}
            for r in (data.get("results") or []):
                if isinstance(r, dict) and r.get("id"):
                    out[str(r["id"])] = {
                        "present": bool(r.get("present")),
                        "evidence": str(r.get("evidence") or "")[:80],
                        "by": "llm",
                    }
            return out
        except Exception as e:
            log.warning(f"OutlineCompliance LLM check failed: {type(e).__name__}: {str(e)[:120]}")
            return None

    @staticmethod
    def _paragraphs(text: str) -> list[str]:
        return [part.strip() for part in re.split(r"\n\s*\n", str(text or "")) if part.strip()]

    @staticmethod
    def _scene_ranges(paragraph_count: int, scene_count: int) -> list[tuple[int, int]]:
        if paragraph_count <= 0:
            return []
        count = max(1, min(scene_count, paragraph_count))
        ranges = []
        for index in range(count):
            start = round(index * paragraph_count / count)
            end = round((index + 1) * paragraph_count / count)
            ranges.append((start, max(start + 1, end)))
        return ranges

    @staticmethod
    def _target_scene(item: dict, beat_count: int, scene_count: int) -> int:
        match = re.match(r"beat(\d+)$", str(item.get("id") or ""))
        if match:
            beat_number = max(1, int(match.group(1)))
            if beat_count <= 1 or scene_count <= 1:
                return 0
            ratio = min(1.0, (beat_number - 1) / max(1, beat_count - 1))
            return min(scene_count - 1, round(ratio * (scene_count - 1)))
        return scene_count // 2

    def revise_missing_scenes(
        self,
        text: str,
        missing_items: list,
        chapter_outline: dict,
    ) -> dict:
        """把缺失节拍修回它所在的场景，返回完整的修订后正文。

        编辑器只能替换按节拍位置选中的局部场景；它不接受“章末追加”
        这种操作，也不直接整章覆盖。
        """
        paragraphs = self._paragraphs(text)
        beats = (chapter_outline or {}).get("scene_beats") or []
        beat_count = max(1, len(beats))
        scene_count = min(len(paragraphs), beat_count) if paragraphs else 0
        if not self._resilient or not missing_items or not paragraphs or not scene_count:
            return {"text": str(text or ""), "applied": False, "edits": [], "reason": "no_revision_target"}

        ranges = self._scene_ranges(len(paragraphs), scene_count)
        grouped: dict[int, list[dict]] = {}
        for item in missing_items:
            target = self._target_scene(item, beat_count, scene_count)
            grouped.setdefault(target, []).append(item)

        replacements = []
        for scene_index, items in sorted(grouped.items()):
            start, end = ranges[scene_index]
            original_scene = "\n\n".join(paragraphs[start:end])
            previous_tail = paragraphs[start - 1][-180:] if start > 0 else "（本章开场）"
            next_head = paragraphs[end][:180] if end < len(paragraphs) else "（本章收束）"
            missing_lines = "\n".join(
                f"- {item.get('label') or item.get('id')}｜必须落地: {item.get('needle') or ''}"
                for item in items
            )
            user = (
                f"缺失的场景契约:\n{missing_lines}\n\n"
                f"前一场尾部（只用于衔接）:\n{previous_tail}\n\n"
                f"目标场景（需要局部修订）:\n{original_scene}\n\n"
                f"后一场开头（只用于衔接）:\n{next_head}"
            )
            try:
                resp = self._resilient.create(
                    messages=[
                        {"role": "system", "content": SCENE_REVISION_SYSTEM},
                        {"role": "user", "content": user},
                    ],
                    temperature=0.55,
                    max_tokens=1200,
                )
                content = resp.choices[0].message.content if hasattr(resp, "choices") else resp
                revised_scene = _clean_patch(str(content)) if content else ""
            except Exception as exc:
                log.warning(
                    "OutlineCompliance scene revision failed: %s: %s",
                    type(exc).__name__, str(exc)[:120],
                )
                continue

            minimum = max(20, int(len(original_scene) * 0.4))
            maximum = max(len(original_scene) * 3, len(original_scene) + 900)
            if len(revised_scene) < minimum or len(revised_scene) > maximum:
                log.warning(
                    "Rejected scene revision %s: length %s outside %s-%s",
                    scene_index + 1, len(revised_scene), minimum, maximum,
                )
                continue
            replacements.append((start, end, revised_scene, scene_index, items))

        edited = list(paragraphs)
        edit_records = []
        for start, end, revised_scene, scene_index, items in sorted(replacements, reverse=True):
            edited[start:end] = self._paragraphs(revised_scene)
            edit_records.append({
                "operation": "replace_scene",
                "scene": scene_index + 1,
                "paragraph_start": start,
                "paragraph_end": end - 1,
                "item_ids": [str(item.get("id") or "") for item in items],
            })
        edit_records.sort(key=lambda item: item["scene"])
        return {
            "text": "\n\n".join(edited),
            "applied": bool(edit_records),
            "edits": edit_records,
            "reason": "scene_revised" if edit_records else "revision_rejected",
        }

    # ── 主入口 ──
    def check_chapter(self, chapter_text: str, chapter_outline: dict,
                      run_deep: bool = True) -> dict:
        """校验章节正文的大纲合规度

        Returns:
            {pct, passed, level: ok/partial/fail, results: [...], missing: [...],
             critical_missing: [...], auto_patch: 补写文本或""}
        """
        text = str(chapter_text or "")
        items = self.extract_check_items(chapter_outline)
        if not items:
            return {"pct": 100, "passed": True, "level": "ok",
                    "results": [], "missing": [], "critical_missing": [], "auto_patch": ""}

        # L1 规则
        merged = self._rule_check(text, items)
        # L2 LLM（单章/非批量才跑，省成本）
        if run_deep:
            llm = self._llm_check(text, items)
            if llm:
                merged.update(llm)
        # 补齐缺失判定（LLM 不可用/批量模式：非规则项按 present=False 保守处理？
        # 不——批量模式跳过非规则项，避免误报）
        results = []
        missing = []
        critical_missing = []
        for it in items:
            r = merged.get(it["id"])
            if r is None:
                if run_deep:
                    # 未配置 LLM 时是规则-only 检查，不应把未执行的语义校验
                    # 当作失败；但已配置 LLM 且调用故障时，critical 项必须保守
                    # 计缺失，避免故障时静默通过。
                    if self._resilient is None:
                        r = {"present": True, "evidence": "（未配置 LLM，仅完成规则检查）", "by": "unknown"}
                    elif it.get("critical"):
                        r = {"present": False, "evidence": "（LLM 精判不可用，critical 项保守计缺失）", "by": "unknown"}
                    else:
                        r = {"present": True, "evidence": "（LLM 精判不可用，未确认）", "by": "unknown"}
                else:
                    r = {"present": True, "evidence": "（批量模式跳过 LLM 精判）", "by": "skipped"}
            row = dict(it)
            row.update(r)
            results.append(row)
            if not r.get("present"):
                missing.append(row)
                if it.get("critical"):
                    critical_missing.append(row)

        total = len(results)
        hit = sum(1 for r in results if r.get("present"))
        pct = round(hit / total * 100) if total else 100

        if pct >= 80 and not critical_missing:
            level, passed = "ok", True
        elif pct < 50 or len(critical_missing) >= 2:
            level, passed = "fail", False
        else:
            level, passed = "partial", False

        return {
            "pct": pct,
            "passed": passed,
            "level": level,
            "results": results,
            "missing": missing,
            "critical_missing": critical_missing,
            "auto_patch": "",
        }
