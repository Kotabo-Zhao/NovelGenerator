#!/usr/bin/env python3
"""
attr_system.py — 互动小说角色属性数值系统（v3.7）

分层混合架构的"状态层-属性"部分：
  - 5 维基础属性（力量/敏捷/智力/魅力/体魄），1-100 数值 + 一句话文本依据
  - 规则推断（确定性、零 LLM 成本）：从角色档案文本关键词映射属性
  - 属性卡渲染：供 dialogue_engine / action_engine 注入 prompt 做生成锚点

设计原则：
  - 数值是"权威状态"，文本是"叙事解释"——两者绑定，修改必同步
  - 推断是启发式规则（可复现、可测试），不做 LLM 猜测
  - 无信号维度取中性 50（不制造虚假区分度）
"""

from typing import Dict, Optional

# ── 5 维属性定义 ──
STAT_DEFS = {
    "力量": {"key": "str", "label": "力量", "desc": "体能强度：打斗、负重、威压"},
    "敏捷": {"key": "dex", "label": "敏捷", "desc": "反应速度：闪避、追击、巧手"},
    "智力": {"key": "int", "label": "智力", "desc": "头脑：谋略、学识、破解"},
    "魅力": {"key": "cha", "label": "魅力", "desc": "气场：说服、威慑、好感"},
    "体魄": {"key": "con", "label": "体魄", "desc": "耐受力：承伤、抗毒、恢复"},
}
STAT_KEYS = ["力量", "敏捷", "智力", "魅力", "体魄"]

# ── 关键词 → 属性增量（命中即加分，文本信号越强分越高）──
_KEYWORD_BOOSTS = {
    "力量": [
        ("剑客|剑修|剑士|刀客|武者|武夫|力士|将军|猛将|悍勇|膂力|蛮力", 35),
        ("侠客|镖师|护卫|士兵|战将|宗师|武学|拳|掌|棍|刀|剑", 25),
        ("猎户|渔夫|铁匠|屠夫|农夫|苦力|搬运|壮硕|魁梧", 15),
    ],
    "敏捷": [
        ("刺客|盗贼|杀手|飞贼|轻功|身法|迅捷|鬼魅|影卫|斥候", 35),
        ("舞者|杂耍|伶人|探子|灵敏|灵活|矫健|疾行", 20),
    ],
    "智力": [
        ("军师|谋士|智者|书生|才子|博士|学士|翰林|帝师|算无遗策", 35),
        ("医者|医师|大夫|炼丹|炼器|阵师|符师|药师|学者|研究|精通", 25),
        ("机巧|傀儡|机关|星象|卦|策|谍报|情报|聪慧|机敏|聪颖", 15),
    ],
    "魅力": [
        ("倾国|倾城|绝色|美人|俊美|英俊|狐媚|魅惑|天香|祸水", 35),
        ("商贾|掌柜|交际|名妓|花魁|歌姬|琴师|说客|辩才|口才|妙语", 25),
        ("温婉|儒雅|风度|亲和|威望|民心|领袖|气质|仪态", 15),
    ],
    "体魄": [
        ("体壮|健壮|强健|皮糙肉厚|铜皮铁骨|金钟罩|铁布衫|不死|再生", 30),
        ("体修|横练|抗揍|耐打|淬体|锻体|药浴", 25),
        ("魁梧|高大|壮实|坚韧|耐力|扛|耐|恢复快", 10),
    ],
}
# 负面关键词 → 属性降低
_KEYWORD_PENALTIES = {
    "力量": [("瘦弱|体弱|文弱|羸弱|纤细|单薄", -20)],
    "敏捷": [("笨拙|迟钝|迟缓|笨重|行动不便|负伤", -20)],
    "智力": [("憨厚|鲁莽|莽撞|愚钝|天真|单纯", -15)],
    "魅力": [("丑陋|凶恶|狰狞|可怖|邋遢|粗鄙|满脸横肉", -20)],
    "体魄": [("病弱|体弱|多病|久病|重伤|残疾|断臂|失明", -25)],
}

# ── 修仙修为境界 → 战力映射（体魄/力量加成）──
_REALM_BOOSTS = {
    "炼气": 5, "练气": 5, "筑基": 10, "结丹": 18, "金丹": 18, "元婴": 28,
    "化神": 40, "炼虚": 52, "合体": 62, "大乘": 75, "渡劫": 85, "仙人": 95, "真仙": 95,
}


def clamp(v: int, lo: int = 1, hi: int = 95) -> int:
    return max(lo, min(hi, int(round(v))))


def infer_stats_from_text(text: str, cultivation: str = "") -> Dict[str, int]:
    """从角色档案文本（identity/personality/backstory 拼接）推断 5 维属性。

    Args:
        text: 角色描述文本（可拼接多个字段）
        cultivation: 修为境界（修仙体系，映射到力量/体魄加成）

    Returns:
        {"力量": int, "敏捷": int, "智力": int, "魅力": int, "体魄": int}
        数值区间 1-95（留 5 分成长空间，避免开局顶格）
    """
    import re
    if not text:
        text = ""
    stats = {k: 50 for k in STAT_KEYS}

    for stat in STAT_KEYS:
        # 主信号取最高档，次级信号按 40% 衰减叠加，单维信号增量封顶 +35
        hit = []
        for pat, boost in _KEYWORD_BOOSTS.get(stat, []):
            if re.search(pat, text):
                hit.append(boost)
        if hit:
            max_b = max(hit)
            others = sum(hit) - max_b
            stats[stat] += min(35, int(max_b + others * 0.4))
        # 负面信号直接叠加（扣分不设下限逻辑，clamp 兜底）
        for pat, penalty in _KEYWORD_PENALTIES.get(stat, []):
            if re.search(pat, text):
                stats[stat] += penalty

    # 修为境界加成（修仙体系：境界越高战力越强，力量/体魄加成封顶 +30）
    if cultivation:
        for realm, boost in _REALM_BOOSTS.items():
            if realm in str(cultivation):
                stats["力量"] += min(30, boost)
                stats["体魄"] += min(15, boost // 2)
                break

    return {k: clamp(v) for k, v in stats.items()}


def infer_stats_from_profile(profile: dict) -> Dict[str, int]:
    """从角色卡 profile 推断属性（profile: identity/personality/backstory/motivation 等）"""
    if not isinstance(profile, dict):
        profile = {}
    parts = []
    for f in ("identity", "personality", "personality_brief", "backstory",
              "motivation", "speak_style", "role", "初始态度", "性格"):
        v = profile.get(f)
        if isinstance(v, str) and v:
            parts.append(v)
    text = " ".join(parts)
    cultivation = ""
    for f in ("cultivation", "修为", "cheat"):
        v = profile.get(f)
        if isinstance(v, str) and v:
            cultivation = v
            break
    return infer_stats_from_text(text, cultivation)


def render_stats_card(stats: Dict[str, int], name: str = "", max_line: int = 3) -> str:
    """渲染属性卡文本（供 prompt 注入）。

    输出示例：
    ## 属性卡（数值权威——言行/判定必须符合）:
    - 力量 78 · 敏捷 55 · 智力 62 · 魅力 80 · 体魄 45
    """
    if not stats:
        return ""
    lines = []
    for k in STAT_KEYS:
        v = stats.get(k)
        if isinstance(v, (int, float)):
            lines.append(f"{k} {int(v)}")
    if not lines:
        return ""
    title = f"## 属性卡（{name}，数值权威——言行与判定必须符合，数值越高越强）:" if name else \
        "## 属性卡（数值权威——言行与判定必须符合，数值越高越强）:"
    return title + "\n" + " · ".join(lines)


# ── 工具：老存档兼容（无 stats 时补默认）──
def ensure_stats(profile: dict, inplace: bool = True) -> Dict[str, int]:
    """确保 profile 带 stats：没有则用规则推断并写入（幂等）。

    Returns:
        stats dict
    """
    if not isinstance(profile, dict):
        profile = {}
    existing = profile.get("stats")
    if isinstance(existing, dict) and all(k in existing for k in STAT_KEYS):
        return existing
    stats = infer_stats_from_profile(profile)
    if inplace:
        profile["stats"] = stats
    return stats
