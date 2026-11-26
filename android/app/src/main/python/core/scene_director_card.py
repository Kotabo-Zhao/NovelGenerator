"""Zero-intervention Dramatic-Vector Director Card generator (v5.1.0).

Produces pure structural dramatic vectors (tactical goals, information asymmetry,
power shift, and 3 raw sensory nouns) WITHOUT pre-written prose sentences or
dialogue lines, preventing mechanical card-echo while forcing vivid subtext.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

log = logging.getLogger(__name__)

_DIRECTOR_SYSTEM = """你是顶级中文小说结构导演。根据本章大纲，为正文写手输出一份「纯戏剧向量卡」（严格 JSON，不要多余解释）。
铁律：
1. 只给戏剧条件和原始名词，绝对不要替正文写好完整描写句或台词，避免正文机械照抄。
2. 每场必须明确：信息差（谁隐瞒了什么/谁误判了什么）、权力/情绪翻转（开场谁占上风→收尾局势如何逆转）。
3. sensory_nouns 只填 3 个具体可感的原始名词（如 ["铁皮檐","焦边纸","旧算盘"]），不加形容词。"""


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
    """Format a structured dramatic-vector card into a compact writer instruction block."""
    if not isinstance(card, dict):
        return ""
    scenes = card.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        return ""
    lines = [
        "## 🎬 本章戏剧向量（用全新原创动作与台词演出来，禁止照抄条目原话）"
    ]
    for idx, sc in enumerate(scenes[:3], 1):
        if not isinstance(sc, dict):
            continue
        goal = str(sc.get("tactical_goal") or "").strip()
        asym = str(sc.get("info_asymmetry") or sc.get("subtext") or "").strip()
        shift = str(sc.get("power_shift") or sc.get("lived_in_detail") or "").strip()
        nouns = sc.get("sensory_nouns")
        if isinstance(nouns, list) and nouns:
            sensory = " / ".join(str(x).strip() for x in nouns[:4] if str(x).strip())
        else:
            sensory = str(sc.get("sensory_anchor") or "").strip()
        parts = []
        if goal:
            parts.append(f"博弈目标：{goal}")
        if asym:
            parts.append(f"信息差与潜台词：{asym}")
        if shift:
            parts.append(f"局势翻转：{shift}")
        if sensory:
            parts.append(f"场景物件锚点：{sensory}")
        if parts:
            lines.append(f"- 场{idx}：" + " ｜ ".join(parts))
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
    """Synchronously generate a compact dramatic-vector card (safe via asyncio.to_thread)."""
    if resilient_client is None or not isinstance(chapter_outline, dict):
        return ""
    title = str(chapter_outline.get("title") or "").strip()
    summary = str(chapter_outline.get("summary") or "").strip()
    events = chapter_outline.get("key_events") or chapter_outline.get("events") or []
    events_str = "；".join(str(e) for e in events[:5]) if isinstance(events, list) else str(events)
    if not summary and not events_str:
        return ""

    taboo_note = f"\n前两章高频套话（禁止沿用）：{'、'.join(recent_cliches[:6])}" if recent_cliches else ""
    user_prompt = (
        f"题材：{genre}｜风格：{style}\n"
        f"章节：{title}\n"
        f"大纲概要：{summary[:450]}\n"
        f"核心事件：{events_str[:300]}\n"
        f"前文衔接参考：{(context_snippet or '')[:320]}{taboo_note}\n\n"
        "请输出 JSON（2-3 场，每项用极简短语，不要写成品句子或台词）：\n"
        '{"scenes":[{"tactical_goal":"双方本场各自想达成什么","info_asymmetry":"谁隐瞒了什么/谁误判了什么","power_shift":"开场态势→收尾翻转","sensory_nouns":["物件1","声响/触感2","环境3"]}]}'
    )

    try:
        resp = resilient_client.create(
            messages=[
                {"role": "system", "content": _DIRECTOR_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.72,
            max_tokens=380,
        )
        raw = resp.choices[0].message.content if resp and getattr(resp, "choices", None) else ""
        card = _extract_json_obj(raw or "")
        return format_director_card(card or {})
    except Exception as exc:
        log.warning("SceneDirectorCard skipped (non-fatal): %s", exc)
        return ""
