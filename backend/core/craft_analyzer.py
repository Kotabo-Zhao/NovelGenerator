"""Zero-LLM-cost millisecond Chinese prose craft analyzer."""

from __future__ import annotations

import math
import re
from typing import Any

CLICHE_GROUPS: dict[str, tuple[str, ...]] = {
    "心理动词直给": (
        "他感到", "她感到", "我感到", "他意识到", "她意识到", "他明白", "她明白",
        "他决定", "她决定", "心中暗想", "心里明白", "不禁想到",
    ),
    "情绪直白标签": (
        "心中充满", "内心深处", "一股难以言喻", "莫名的情绪", "复杂的情绪",
        "百感交集", "五味杂陈", "悲伤不已", "愤怒不已", "心中一紧",
    ),
    "套路动作神态": (
        "嘴角勾起一抹", "眼中闪过一丝", "深吸一口气", "缓缓开口", "淡淡地说道",
        "微微一笑", "眉头微皱", "空气仿佛凝固", "瞳孔骤缩", "倒吸一口凉气",
    ),
    "AI总结过渡词": (
        "与此同时", "在这个过程中", "随着……的发展", "这不仅仅", "标志着",
        "值得一提的是", "本质上", "毫无疑问", "不可否认", "总而言之",
    ),
}

SENSORY_CUES: tuple[str, ...] = (
    "声音", "响声", "脚步", "呼吸", "低语", "嘶哑", "风声", "雨声", "回声",
    "气味", "香味", "血腥", "铁锈", "潮湿", "烟味", "苦涩", "咸味",
    "冰冷", "滚烫", "刺痛", "粗糙", "发麻", "发抖", "汗水", "微凉", "灼热",
)


def analyze_prose_craft(text: str) -> dict[str, Any]:
    """Evaluate Chinese fiction prose quality offline in milliseconds."""
    clean = (text or "").strip()
    if not clean:
        return {
            "craft_score": 0,
            "ai_score": 0,
            "char_count": 0,
            "paragraph_count": 0,
            "dialogue_ratio_pct": 0.0,
            "sentence_rhythm_cv": 0.0,
            "sensory_per_1k": 0.0,
            "category_hits": {},
            "flagged_sections": [],
            "suggestions": [],
            "suggested_revision_prompt": "",
            "summary": "正文为空",
        }

    flagged: list[dict[str, Any]] = []
    category_hits: dict[str, int] = {}
    total_hits = 0

    for category, patterns in CLICHE_GROUPS.items():
        cat_count = 0
        for pat in patterns:
            cnt = clean.count(pat)
            if cnt > 0:
                cat_count += cnt
                total_hits += cnt
                idx = clean.find(pat)
                excerpt = clean[max(0, idx - 6):min(len(clean), idx + len(pat) + 12)].replace("\n", " ")
                flagged.append({
                    "excerpt": excerpt,
                    "pattern": pat,
                    "category": category,
                    "count": cnt,
                    "reason": f"{category}：「{pat}」（{cnt}次）",
                    "severity": "high" if cnt >= 2 else "medium",
                })
        if cat_count > 0:
            category_hits[category] = cat_count

    raw_sentences = re.split(r"[。！？!?…]+", clean)
    sent_lengths = [len(s.strip()) for s in raw_sentences if len(s.strip()) >= 2]
    rhythm_cv = 0.0
    if len(sent_lengths) >= 4:
        mean_len = sum(sent_lengths) / len(sent_lengths)
        if mean_len > 0:
            var = sum((n - mean_len) ** 2 for n in sent_lengths) / len(sent_lengths)
            rhythm_cv = round(math.sqrt(var) / mean_len, 2)
            if rhythm_cv < 0.35:
                flagged.append({
                    "excerpt": "整体句长分布",
                    "pattern": "rhythm_flat",
                    "category": "节奏单调",
                    "count": len(sent_lengths),
                    "reason": f"句长过于均匀（变异系数 {rhythm_cv:.2f} < 0.35）",
                    "severity": "high",
                })

    dialogue_matches = re.findall(r'[“「『"][^”」』"]+[”」』"]', clean)
    dialogue_chars = sum(len(m) for m in dialogue_matches)
    dialogue_ratio = round(dialogue_chars / max(len(clean), 1) * 100, 1)

    sensory_hits = sum(clean.count(cue) for cue in SENSORY_CUES)
    sensory_per_1k = round(sensory_hits / max(len(clean) / 1000.0, 0.2), 1)
    paragraphs = [p.strip() for p in clean.split("\n") if p.strip()]

    cliche_density = total_hits / max(len(clean) / 1000.0, 0.5)
    ai_score = min(100, int(cliche_density * 14) + (20 if rhythm_cv and rhythm_cv < 0.35 else 0))
    craft_score = max(0, min(100, 100 - ai_score - (8 if len(clean) >= 600 and sensory_per_1k < 1.0 else 0)))

    suggestions: list[str] = []
    revision_parts: list[str] = []
    if category_hits.get("心理动词直给") or category_hits.get("情绪直白标签"):
        suggestions.append("删去“感到/意识到/心中充满”等心理直述，改用具体动作、停顿或物件细节传递情绪。")
        revision_parts.append("删去心理动词直述，改用具体动作、停顿和细节展现情绪")
    if category_hits.get("套路动作神态"):
        top = [f["pattern"] for f in flagged if f["category"] == "套路动作神态"][:3]
        suggestions.append(f"替换高频套路动作（{'、'.join(top)}），改写为贴合当前场景的具象动作。")
        revision_parts.append(f"替换“{'、'.join(top)}”等套路动作，改为具体场景动作")
    if category_hits.get("AI总结过渡词"):
        suggestions.append("去掉“与此同时/这不仅仅/标志着”等总结过渡句，直接切入下一镜头并保留留白。")
        revision_parts.append("删去过渡和总结套话，直接切入场景并保留留白")
    if rhythm_cv and rhythm_cv < 0.35:
        suggestions.append("句长过于均匀，建议在冲突处插入 10 字以内短句，拉开长短句呼吸感。")
        revision_parts.append("拉开长短句节奏，在冲突转折处加入短句锤击")
    if len(clean) >= 600 and sensory_per_1k < 1.0:
        suggestions.append("非视觉感官偏弱，建议补充 1–2 处声音、气味、温度或触感细节。")
        revision_parts.append("补充 1–2 处声音、气味或触感细节增强镜头感")
    if len(clean) >= 800 and dialogue_ratio < 10.0:
        suggestions.append(f"对话占比仅 {dialogue_ratio}%，可适当加入带潜台词的人物交锋。")

    flagged.sort(key=lambda x: (0 if x["severity"] == "high" else 1, -x["count"]))
    summary = (
        f"文风质感 {craft_score}/100，AI 痕迹 {ai_score}/100（命中 {total_hits} 处套话，对话 {dialogue_ratio}%）"
        if total_hits or ai_score > 0
        else f"文风质感 {craft_score}/100，未检出明显 AI 套话（对话 {dialogue_ratio}%，节奏 CV {rhythm_cv:.2f}）"
    )
    return {
        "craft_score": craft_score,
        "ai_score": ai_score,
        "char_count": len(clean),
        "paragraph_count": len(paragraphs),
        "dialogue_ratio_pct": dialogue_ratio,
        "sentence_rhythm_cv": rhythm_cv,
        "sensory_per_1k": sensory_per_1k,
        "category_hits": category_hits,
        "flagged_sections": flagged[:10],
        "suggestions": suggestions,
        "suggested_revision_prompt": "；".join(revision_parts) + "。" if revision_parts else "",
        "summary": summary,
    }
