"""Human-voice realism guardrails and rolling anti-repetition for Roleplay mode."""
from __future__ import annotations

from typing import Any, Dict, List

_RP_CLICHES = (
    "嘴角勾起一抹", "眼中闪过一丝", "深吸一口气", "缓缓开口", "淡淡地说道",
    "微微一笑", "眉头微皱", "空气仿佛凝固", "瞳孔骤缩", "心中一紧",
    "心中涌起一阵", "不由得", "低沉而富有磁性", "声音低哑", "唇角微扬",
    "眼底闪过", "轻笑一声", "顿了顿", "沉默片刻", "仿佛能看穿",
)

ROLEPLAY_HUMAN_VOICE_RULES = (
    "[自然人声准则]\n"
    "1. 严禁替 {{user}} 说话、做决定或描写 {{user}} 的心理与动作，只从当前角色视角回应。\n"
    "2. 禁止复用套路化神态（如嘴角勾起一抹、眼中闪过一丝、空气仿佛凝固、低沉而富有磁性、心中涌起一阵），"
    "用贴合当下处境的具体小动作、停顿、呼吸感与潜台词展现情绪。\n"
    "3. 对话口语自然，长短句错落，不要每轮结尾都总结升华或强行抛问句。"
)


def detect_recent_rp_cliches(history: List[Dict[str, Any]], last_n: int = 3) -> str:
    """Inspect the last N assistant replies and return a compact anti-repetition reminder."""
    if not history:
        return ""
    assistant_texts = [
        str(m.get("content") or "")
        for m in history
        if isinstance(m, dict) and m.get("role") == "assistant" and str(m.get("content") or "").strip()
    ][-last_n:]
    if not assistant_texts:
        return ""
    combined = "\n".join(assistant_texts)
    found = [phrase for phrase in _RP_CLICHES if phrase in combined]
    if not found:
        return ""
    return f"[防复读提醒] 前几轮已出现以下表达，本轮禁止再用：{'、'.join(found[:6])}。"
