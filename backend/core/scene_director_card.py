"""Zero-intervention Dramatic-Vector Director Card with Verbalized Sampling (v5.2.0).

Incorporates Verbalized Sampling (arXiv:2510.01171, ICML 2026) inside a single JSON
call (zero extra LLM calls): the director enumerates 2-3 candidate dramatic angles
with explicit typicality probability `p`, automatically discards the cliché mode,
and selects the lowest-typicality coherent dramatic vector (tactical goals, info
asymmetry, power shift, and 3 raw sensory nouns) without pre-written prose lines.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

log = logging.getLogger(__name__)

_DIRECTOR_SYSTEM = """你是顶级中文小说结构导演。根据本章大纲，采用「言语化采样（Verbalized Sampling）」在同一次 JSON 中先构思 3 种不同的戏剧推进切入点并估算各自的俗套典型概率 p（总和约 1.0），然后重点把最低典型度（p <= 0.30、反套路但逻辑自洽）的方案拆成纯戏剧向量。
铁律：
1. 只给戏剧条件和原始名词，绝对不要替正文写好完整描写句或台词，避免正文机械照抄。
2. 每场必须明确：博弈目标、信息差与潜台词、权力/情绪翻转。
3. sensory_nouns 只填 3 个具体可感的原始名词（如 ["铁皮檐","焦边纸","旧算盘"]），不加形容词。
4. 只输出合法 JSON，不要任何解释。"""


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


def select_verbalized_candidate(card: dict[str, Any]) -> dict[str, Any]:
    """Pick the lowest-typicality valid candidate from a Verbalized Sampling payload.

    Backward-compatible with direct `{"scenes": [...]}` payloads.
    """
    if not isinstance(card, dict):
        return {}
    candidates = card.get("candidates")
    if isinstance(candidates, list) and candidates:
        valid = [
            c for c in candidates
            if isinstance(c, dict) and isinstance(c.get("scenes"), list) and c["scenes"]
        ]
        if valid:
            def _prob(item: dict[str, Any]) -> float:
                try:
                    return float(item.get("p", 0.5))
                except (TypeError, ValueError):
                    return 0.5
            valid.sort(key=_prob)
            return valid[0]
    return card


def format_director_card(card: dict[str, Any]) -> str:
    """Format a structured dramatic-vector card into a compact writer instruction block."""
    chosen = select_verbalized_candidate(card)
    if not isinstance(chosen, dict):
        return ""
    scenes = chosen.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        return ""
    angle = str(chosen.get("angle") or "").strip()
    header = "## 🎬 本章戏剧向量（用全新原创动作与台词演出来，禁止照抄条目原话）"
    if angle:
        header += f"\n- 反套路切入视角：{angle}"
    lines = [header]
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
    """Synchronously generate a Verbalized-Sampling dramatic-vector card."""
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
        f"前文衔接参考：{(context_snippet or '')[:300]}{taboo_note}\n\n"
        "请输出 JSON（给出 2-3 个不同切入点及典型概率 p，只为低典型度反套路方案展开 2-3 场向量）：\n"
        '{"candidates":[{"p":0.65,"angle":"最常见俗套写法（简述）","scenes":[]},'
        '{"p":0.20,"angle":"反套路切入视角","scenes":[{"tactical_goal":"双方本场各自想达成什么","info_asymmetry":"谁隐瞒了什么/谁误判了什么","power_shift":"开场态势→收尾翻转","sensory_nouns":["物件1","声响2","环境3"]}]}]}'
    )

    try:
        resp = resilient_client.create(
            messages=[
                {"role": "system", "content": _DIRECTOR_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.78,
            max_tokens=480,
        )
        raw = resp.choices[0].message.content if resp and getattr(resp, "choices", None) else ""
        card = _extract_json_obj(raw or "")
        return format_director_card(card or {})
    except Exception as exc:
        log.warning("SceneDirectorCard skipped (non-fatal): %s", exc)
        return ""
