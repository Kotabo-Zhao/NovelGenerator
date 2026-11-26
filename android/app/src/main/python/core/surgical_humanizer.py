"""Zero-intervention paragraph-level surgical humanizer & rolling anti-cliche memory.

Scores every paragraph locally (zero LLM cost) for AI-smell signals and sends ONLY
the top 3-4 worst paragraphs to a tight line-editor pass, preserving 100% of plot
facts while upgrading human texture and voice.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

log = logging.getLogger(__name__)

_CLICHE_PHRASES = (
    "他感到", "她感到", "我感到", "他意识到", "她意识到", "他明白", "她明白",
    "心中暗想", "心里明白", "不禁想到", "心中充满", "内心深处", "一股难以言喻",
    "莫名的情绪", "复杂的情绪", "百感交集", "五味杂陈", "悲伤不已", "愤怒不已",
    "心中一紧", "嘴角勾起一抹", "眼中闪过一丝", "深吸一口气", "缓缓开口",
    "淡淡地说道", "微微一笑", "眉头微皱", "空气仿佛凝固", "瞳孔骤缩",
    "倒吸一口凉气", "与此同时", "在这个过程中", "这不仅仅", "标志着",
    "值得一提的是", "本质上", "毫无疑问", "不可否认", "总而言之",
    "掀起了惊涛骇浪", "宛如一潭死水", "不自觉地",
)

_SENSORY_TOKENS = (
    "冷", "热", "烫", "凉", "潮", "湿", "干", "涩", "腥", "香", "臭", "苦", "咸",
    "亮", "暗", "灰", "红", "青", "黑", "白", "光", "影", "声", "响", "咔", "砰",
    "风", "雨", "汗", "血", "灰尘", "木头", "铁", "茶", "烟", "指尖", "掌心", "喉结",
)

_LINE_EDITOR_SYSTEM = """你是顶级中文文学责编，专精“去 AI 味微创润色”。
任务：只对提供的几个高 AI 味段落进行句式与质感微调，让文字像真人作家亲笔写出。
铁律：
1. 严禁改动任何剧情事实、人物关系、物品归属、时间地点与台词核心含义。
2. 删掉“他感到/心中充满/嘴角勾起一抹/眼中闪过一丝/与此同时/毫无疑问”等 AI 套话，改为具体的身体小动作、眼神停顿、环境触感或对话潜台词。
3. 打破整齐划一的排比和总结句，长短句自然错落；结尾不要升华大道理。
4. 每段字数保持在原段的 75%-135% 之间。
5. 只输出合法 JSON 对象，key 为输入中的段落编号字符串，value 为润色后的该段正文。"""


def extract_top_cliches(text: str, limit: int = 8) -> list[str]:
    """Return the most frequent AI cliche phrases found in a chapter."""
    if not text:
        return []
    hits = []
    for phrase in _CLICHE_PHRASES:
        cnt = text.count(phrase)
        if cnt > 0:
            hits.append((cnt, phrase))
    hits.sort(key=lambda item: (-item[0], item[1]))
    return [p for _, p in hits[:limit]]


def record_rolling_cliches(memory: Any, novel_id: str, chapter_num: int, text: str) -> list[str]:
    """Persist detected cliches into global_state['recent_cliches'] for rolling suppression."""
    found = extract_top_cliches(text, limit=8)
    if not found or memory is None:
        return found
    try:
        def _upd(gs):
            if not isinstance(gs, dict):
                gs = {}
            bucket = gs.setdefault("recent_cliches", {})
            if isinstance(bucket, dict):
                bucket[str(chapter_num)] = found
                # Keep only the latest 5 chapters of history
                keys = sorted(bucket.keys(), key=lambda k: int(k) if str(k).isdigit() else 0)
                for old_k in keys[:-5]:
                    bucket.pop(old_k, None)
            return gs
        memory.update("global_state", novel_id, _upd)
    except Exception as exc:
        log.debug("record_rolling_cliches skipped: %s", exc)
    return found


def get_rolling_cliches(memory: Any, novel_id: str, chapter_num: int, window: int = 2) -> list[str]:
    """Read top cliches from the previous `window` chapters."""
    if memory is None or chapter_num <= 1:
        return []
    try:
        gs = memory.read("global_state", novel_id) or {}
        bucket = gs.get("recent_cliches") if isinstance(gs, dict) else None
        if not isinstance(bucket, dict):
            return []
        seen: list[str] = []
        for ch in range(max(1, chapter_num - window), chapter_num):
            for phrase in bucket.get(str(ch)) or []:
                if phrase not in seen:
                    seen.append(phrase)
        return seen[:10]
    except Exception:
        return []


def build_rolling_cliche_block(memory: Any, novel_id: str, chapter_num: int) -> str:
    """Format a compact one-line suppression note for the writer context."""
    recent = get_rolling_cliches(memory, novel_id, chapter_num, window=2)
    if not recent:
        return ""
    return (
        "## 🚫 跨章防复读提醒\n"
        f"前两章已高频出现以下套话，本章严禁再次使用：{'、'.join(recent[:8])}。"
    )


def score_paragraphs(
    text: str,
    recent_cliches: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Score every non-heading paragraph locally for AI-smell signals."""
    paragraphs = [p for p in (text or "").split("\n\n")]
    recent_set = set(recent_cliches or [])
    scored: list[dict[str, Any]] = []
    prev_opener = ""

    for idx, raw_p in enumerate(paragraphs):
        p = raw_p.strip()
        if not p or p.startswith("#") or len(p) < 28:
            prev_opener = ""
            continue
        score = 0
        reasons: list[str] = []

        cliche_hits = [c for c in _CLICHE_PHRASES if c in p]
        if cliche_hits:
            score += len(cliche_hits) * 2
            reasons.append("套话:" + "、".join(cliche_hits[:3]))

        recent_hits = [c for c in recent_set if c in p]
        if recent_hits:
            score += len(recent_hits) * 2
            reasons.append("跨章复读:" + "、".join(recent_hits[:2]))

        opener = p[:2]
        if prev_opener and opener == prev_opener and opener not in ("“", "「", "『"):
            score += 1
            reasons.append(f"连续段首重复({opener})")
        prev_opener = opener

        # Long non-dialogue paragraph with zero concrete sensory anchors -> telling/summary risk
        has_dialogue = ("“" in p or "「" in p or '"' in p)
        has_sensory = any(tok in p for tok in _SENSORY_TOKENS)
        if len(p) >= 130 and not has_dialogue and not has_sensory:
            score += 2
            reasons.append("长段纯概括缺感官")

        if score > 0:
            scored.append({
                "index": idx,
                "score": score,
                "text": p,
                "reasons": reasons,
            })

    scored.sort(key=lambda item: (-item["score"], item["index"]))
    return scored


def _parse_json_map(raw: str) -> dict[str, str]:
    text = (raw or "").strip()
    if not text:
        return {}
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    try:
        data = json.loads(text)
    except Exception:
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return {}
        try:
            data = json.loads(m.group(0))
        except Exception:
            return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v).strip() for k, v in data.items() if isinstance(v, str) and v.strip()}


def polish_flagged_paragraphs(
    resilient_client: Any,
    model: str,
    text: str,
    *,
    max_paragraphs: int = 4,
    min_score: int = 3,
    recent_cliches: list[str] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Surgically polish only the top high-AI-smell paragraphs; skip LLM if clean."""
    if resilient_client is None or not (text or "").strip():
        return text, {"polished": 0, "skipped": True}

    candidates = [
        item for item in score_paragraphs(text, recent_cliches=recent_cliches)
        if item["score"] >= min_score
    ][:max_paragraphs]

    if not candidates:
        return text, {"polished": 0, "skipped": True}

    paragraphs = text.split("\n\n")
    payload = {str(item["index"]): item["text"] for item in candidates}
    hints = "\n".join(
        f"- 段落 {item['index']} 问题：{'；'.join(item['reasons'])}" for item in candidates
    )
    user_prompt = (
        f"以下 {len(candidates)} 个段落检测到较重 AI 味或套话，请原地微创润色：\n"
        f"{hints}\n\n"
        f"待润色段落 JSON：\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
        "请只输出 JSON 对象（key 保持原段落编号字符串，value 为润色后正文）："
    )

    try:
        resp = resilient_client.create(
            messages=[
                {"role": "system", "content": _LINE_EDITOR_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.72,
            max_tokens=min(2400, max(600, sum(len(item["text"]) for item in candidates) * 2)),
        )
        raw = resp.choices[0].message.content if resp and getattr(resp, "choices", None) else ""
        edited_map = _parse_json_map(raw or "")
    except Exception as exc:
        log.warning("SurgicalHumanizer skipped (non-fatal): %s", exc)
        return text, {"polished": 0, "skipped": True, "error": str(exc)}

    polished_indices: list[int] = []
    for item in candidates:
        idx = item["index"]
        new_p = edited_map.get(str(idx), "")
        orig_len = max(1, len(item["text"]))
        # Guardrail: prevent accidental truncation, bloat, or meta leakage
        if (
            new_p
            and 0.55 * orig_len <= len(new_p) <= 1.85 * orig_len
            and not new_p.startswith("#")
        ):
            paragraphs[idx] = new_p
            polished_indices.append(idx)

    if not polished_indices:
        return text, {"polished": 0, "skipped": False}

    return "\n\n".join(paragraphs), {
        "polished": len(polished_indices),
        "indices": polished_indices,
        "skipped": False,
    }
