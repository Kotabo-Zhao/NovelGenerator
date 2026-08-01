"""VoiceDirector — 角色音色映射（v3.0）

人设 → edge-tts 音色自动匹配（规则版）+ 玩家覆盖（voice_overrides.json）。

规则映射基于性别/年龄/性格关键词（docs/interactive-novel-plan.html §7）：
- 玩家覆盖 > 角色卡 voice 字段 > 规则映射 > 默认
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
DEFAULT_MALE = "zh-CN-YunxiNeural"

# (性别, 年龄/性格关键词列表, 音色, rate, pitch)
_RULES = [
    # 女性
    ("女", ["萝莉", "小孩", "年幼", "少女", "可爱", "活泼"], "zh-CN-XiaomengNeural", "+15%", "+30Hz"),
    ("女", ["少年", "年轻", "俏皮", "活泼", "开朗", "元气"], "zh-CN-XiaoyiNeural", "+10%", "+20Hz"),
    ("女", ["御姐", "成熟", "冷艳", "魅惑", "妖", "风情"], "zh-CN-XiaomoNeural", "-5%", "-10Hz"),
    ("女", ["温柔", "贤惠", "温婉", "大家闺秀", "医者"], "zh-CN-XiaoxiaoNeural", "+0%", "+0Hz"),
    ("女", ["清冷", "仙子", "高冷", "疏离", "出尘", "圣女"], "zh-CN-XiaoxuanNeural", "-10%", "-20Hz"),
    ("女", ["知性", "沉稳", "女王", "掌权", "成熟女性"], "zh-CN-XiaoruiNeural", "-5%", "-15Hz"),
    ("女", ["甜美", "软萌", "温柔妹妹"], "zh-CN-XiaoxuanNeural", "+10%", "+20Hz"),
    # 男性
    ("男", ["少年", "男孩", "年轻", "稚气", "弟弟"], "zh-CN-YunxiaNeural", "+10%", "+20Hz"),
    ("男", ["大叔", "沉稳", "壮汉", "中年", "将领", "父亲"], "zh-CN-YunjianNeural", "-15%", "-25Hz"),
    ("男", ["邪魅", "反派", "阴冷", "枭雄", "魔头"], "zh-CN-YunyeNeural", "-10%", "-15Hz"),
    ("男", ["儒雅", "书生", "文士", "斯文", "温柔男"], "zh-CN-YunxiNeural", "+0%", "+0Hz"),
    ("男", ["阳光", "开朗", "热血", "青年"], "zh-CN-YunxiNeural", "+10%", "+5Hz"),
]


def match_voice(profile_desc: str, gender: str = "") -> dict:
    """规则映射：人设描述 → 音色配置

    profile_desc: 性格/身份描述文本（可空）
    gender: "男"/"女"/""（未知）
    返回: {"voice": "...", "rate": "...", "pitch": "..."}
    """
    desc = (profile_desc or "").strip()
    # 先按性别+关键词规则
    for g, kws, voice, rate, pitch in _RULES:
        if gender and g != gender:
            continue
        for kw in kws:
            if kw in desc:
                return {"voice": voice, "rate": rate, "pitch": pitch}
    # 性别兜底
    if gender == "男":
        return {"voice": DEFAULT_MALE, "rate": "+0%", "pitch": "+0Hz"}
    if gender == "女":
        return {"voice": DEFAULT_VOICE, "rate": "+0%", "pitch": "+0Hz"}
    return {"voice": DEFAULT_VOICE, "rate": "+0%", "pitch": "+0Hz"}


def resolve_voice(store, novel_id: str, char_name: str,
                  profile_desc: str = "", gender: str = "") -> dict:
    """解析角色最终音色：玩家覆盖 > 规则映射 > 默认"""
    # 1. 玩家覆盖（最高优先级）
    try:
        overrides = store.get_voice_overrides(novel_id)
        if char_name in overrides:
            ov = overrides[char_name]
            return {
                "voice": ov.get("voice", DEFAULT_VOICE),
                "rate": ov.get("rate", "+0%"),
                "pitch": ov.get("pitch", "+0Hz"),
                "source": "player",
            }
    except Exception as e:
        log.warning(f"resolve_voice overrides failed: {e}")
    # 2. 规则映射
    cfg = match_voice(profile_desc, gender)
    cfg["source"] = "rule"
    return cfg
