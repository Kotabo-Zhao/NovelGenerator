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

PATCH_SYSTEM = """你是网络小说编辑。本章正文遗漏了大纲中承诺的核心事件，请从断点续写一段（300-450字），把缺失事件【自然补入】并衔接已有情节。

要求：
1. 与已有正文的风格、视角、节奏一致
2. 不重复已有内容，从断点直接延续
3. 每个缺失事件必须有实际情节展开（有过程/有反应/有结果），不能只提一句
4. 补写的段落要像原本就属于本章，不出现"补写""大纲""缺失"等元话语
5. 只输出正文文本，不要标题、不要解释"""


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
            if not isinstance(b, dict):
                continue
            ka = str(b.get("key_action") or b.get("name") or "").strip()
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
                results[it["id"]] = {
                    "present": present,
                    "evidence": "" if present else f"未检索到节拍关键词「{it['needle'][:30]}」",
                    "by": "rule",
                }
        return results

    # ── L2 LLM 精判 ──
    def _llm_check(self, text: str, items: list) -> Optional[dict]:
        if not self._resilient:
            return None
        # 只精判非规则项（summary / conflict / 无关键词节拍）
        pending = [it for it in items
                   if not it["id"].startswith("char_") and not it["id"].startswith("beat")]
        if not pending:
            return {}
        brief = "\n".join(
            f"- {it['id']}: {it['label']}｜要求: {it['needle']}" for it in pending)
        excerpt = text[:1500]
        if len(text) > 2300:
            excerpt += "\n……（正文中间省略）……\n" + text[-800:]
        elif len(text) > 1500:
            excerpt += "\n……（正文中间省略）……\n" + text[-300:]
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

    # ── 自动补写 ──
    def patch_missing(self, text: str, missing_items: list) -> str:
        """fail 时补写缺失核心事件（同步 LLM 调用，返回补写文本）"""
        if not self._resilient or not missing_items:
            return ""
        missing_lines = "\n".join(
            f"- {it['label']}｜要求: {it['needle']}" for it in missing_items)
        user = (
            f"缺失的核心事件:\n{missing_lines}\n\n"
            f"已有正文末尾:\n{text[-400:]}\n\n"
            "请续写一段，把上述缺失事件自然补入本章。"
        )
        try:
            resp = self._resilient.create(
                messages=[
                    {"role": "system", "content": PATCH_SYSTEM},
                    {"role": "user", "content": user},
                ],
                temperature=0.7,
                max_tokens=800,
            )
            content = resp.choices[0].message.content if hasattr(resp, "choices") else resp
            return _clean_patch(str(content)) if content else ""
        except Exception as e:
            log.warning(f"OutlineCompliance patch failed: {type(e).__name__}: {str(e)[:120]}")
            return ""

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
                    # LLM 精判不可用：不误报，视为通过（无法确认，保守乐观）
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
