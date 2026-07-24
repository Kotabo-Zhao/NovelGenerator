"""NovelGenerator — BeatDecomposer: 章节→节拍拆解器

将一章大纲拆解为 5-7 个独立节拍（beat），每个 beat 有独立的功能、情绪弧、冲突类型、钩子类型。
这是原子化生成的第一步：拆得越碎，随机性空间越大。
"""
import json
import random
import logging
from typing import Optional

log = logging.getLogger(__name__)

# ── Beat 数据结构 ──

BEAT_FUNCTIONS = [
    "opening_hook",       # 开篇钩子：300字内抓住读者
    "obstacle_build",     # 障碍堆叠：让读者揪心
    "conflict_ignition",  # 冲突引爆：本章核心矛盾爆发
    "turning_point",      # 转折点：意外/反转
    "character_highlight",# 人设高光：展示角色特质
    "climax_release",     # 高潮释放：爽感顶点
    "closing_hook",       # 章末钩子：让读者点下一章
    "emotion_settle",     # 情绪沉降：缓冲/留白
    "info_reveal",        # 信息揭示：新线索/秘密
    "setup_payoff",       # 伏笔回收：兑现前文暗示
]

HOOK_TYPES = ["crisis_suspense", "reversal_tease", "face_slap_preview", "secret_reveal"]

CONFLICT_TYPES = ["IN", "IR", "EN", "DE"]  # 内心/人际/环境/宿命

BEAT_TEMPLATES = {
    "opening_hook": {
        "goal": "立刻建立冲突或悬念，让读者想知道'然后呢'",
        "emotion_start": "紧张/好奇",
        "emotion_end": "好奇/紧张",
        "min_words": 120,
        "max_words": 300,
        "temperature": 0.9,
        "hook_required": False,
        "paragraph_style": "节奏型：1-2句短句开场 → 中间2-3句展开 → 1句收束。每段2-3句，不要单句成段。",
    },
    "obstacle_build": {
        "goal": "堆叠障碍，制造'为什么这么难/这么不公平'的感觉",
        "emotion_start": "压抑/挫折",
        "emotion_end": "压抑/期待反弹",
        "min_words": 180,
        "max_words": 400,
        "temperature": 0.85,
        "hook_required": False,
        "paragraph_style": "描写型：用具体的环境/身体感受来写压抑，不要用抽象形容词。每段≥2句。用1-2句超过25字的长句来营造沉重感。",
    },
    "conflict_ignition": {
        "goal": "核心矛盾在此爆发，正面对决/价值观碰撞/意志对抗",
        "emotion_start": "紧张/对抗",
        "emotion_end": "爆发/震撼",
        "min_words": 200,
        "max_words": 450,
        "temperature": 0.9,
        "hook_required": False,
        "paragraph_style": "冲击型：≤8字的短句占比≤30%。每3句短句后必须有1句≥20字的长句描写缓冲。禁止连续4句短句。每段2-3句。",
    },
    "turning_point": {
        "goal": "意外的转折——预期违背/身份揭露/新信息改变一切",
        "emotion_start": "意外/困惑",
        "emotion_end": "恍然/震惊",
        "min_words": 150,
        "max_words": 350,
        "temperature": 0.95,
        "hook_required": False,
        "paragraph_style": "揭晓型：先用2-3句铺垫当前的认知 → 1-2句短句炸开转折 → 再用3-4句展开新认知的含义。禁止一句话一个段落。",
    },
    "character_highlight": {
        "goal": "通过一个选择或行动，展示角色最核心的特质",
        "emotion_start": "共鸣/欣赏",
        "emotion_end": "共鸣/记忆",
        "min_words": 180,
        "max_words": 350,
        "temperature": 0.85,
        "hook_required": False,
        "paragraph_style": "描写型：用具体的行为/选择/细节来展示性格。每段2-4句。允许内心独白（1-2句），但不能全是内心戏。禁止全短句。",
    },
    "climax_release": {
        "goal": "爽感顶点——打脸/碾压/告白/胜利，给读者释放感",
        "emotion_start": "高潮/释放",
        "emotion_end": "满足/余韵",
        "min_words": 180,
        "max_words": 400,
        "temperature": 0.85,
        "hook_required": False,
        "paragraph_style": "释放型：先短句制造冲击（占比≤40%）→ 中段用描写+反应展开 → 结尾1-2句长句收束。短句不超过连续的3句。",
    },
    "closing_hook": {
        "goal": "在最高点截断——危机悬停/反转预告/打脸前置/秘密揭晓",
        "emotion_start": "满足/好奇",
        "emotion_end": "强烈好奇/必看",
        "min_words": 80,
        "max_words": 180,
        "temperature": 0.9,
        "hook_required": True,
        "paragraph_style": "钩子型：先1-2段交代当前状态（每段≥2句），最后1-2句短句做钩子收尾。钩子前面的内容不能也是短句。",
    },
    "emotion_settle": {
        "goal": "战斗/冲突后的缓冲——环境描写、日常细节、内心反思",
        "emotion_start": "疲惫/释放",
        "emotion_end": "平静/反思",
        "min_words": 150,
        "max_words": 300,
        "temperature": 0.75,
        "hook_required": False,
        "paragraph_style": "沉浸型：这是缓冲节拍，必须用长段落。每段≥3句，用≥25字的长句写环境/感受/回忆。禁止短句。人物可以发呆、看风景、想事情。",
    },
    "info_reveal": {
        "goal": "揭示新线索、秘密或世界观规则，让读者'哦！原来如此'",
        "emotion_start": "好奇/困惑",
        "emotion_end": "恍然/新好奇",
        "min_words": 150,
        "max_words": 350,
        "temperature": 0.85,
        "hook_required": False,
        "paragraph_style": "信息型：1-2段描写发现过程（每段≥2句）→ 1段展开信息含义 → 最后1句埋下新疑问。禁止干巴巴罗列信息。",
    },
    "setup_payoff": {
        "goal": "回收一个前文伏笔，给忠实读者'我就知道'的满足感",
        "emotion_start": "似曾相识/期待",
        "emotion_end": "满足/闭环",
        "min_words": 120,
        "max_words": 250,
        "temperature": 0.8,
        "hook_required": False,
        "paragraph_style": "闭环型：先1-2句暗示前文（不直说），然后2-3句展开回收过程，最后1句落定。每段2-3句。",
    },
}


class Beat:
    """一个节拍"""
    __slots__ = ("index", "function", "goal", "emotion_start", "emotion_end",
                 "conflict_type", "conflict_intensity", "hook_type",
                 "min_words", "max_words", "temperature",
                 "outline_ref", "character_focus", "key_event",
                 "paragraph_style")
    
    def __init__(self, index: int, function: str):
        self.index = index
        self.function = function
        tmpl = BEAT_TEMPLATES.get(function, BEAT_TEMPLATES["conflict_ignition"])
        self.goal = tmpl["goal"]
        self.emotion_start = tmpl["emotion_start"]
        self.emotion_end = tmpl["emotion_end"]
        self.min_words = tmpl["min_words"]
        self.max_words = tmpl["max_words"]
        self.temperature = tmpl["temperature"]
        self.paragraph_style = tmpl.get("paragraph_style", "")
        self.conflict_type = "IR"
        self.conflict_intensity = 3
        self.hook_type = None
        self.outline_ref = ""
        self.character_focus = ""
        self.key_event = ""
    
    def to_dict(self) -> dict:
        return {
            "index": self.index, "function": self.function,
            "goal": self.goal, "emotion_start": self.emotion_start,
            "emotion_end": self.emotion_end, "conflict_type": self.conflict_type,
            "conflict_intensity": self.conflict_intensity, "hook_type": self.hook_type,
            "min_words": self.min_words, "max_words": self.max_words,
            "temperature": self.temperature,
            "outline_ref": self.outline_ref, "character_focus": self.character_focus,
            "key_event": self.key_event,
        }
    
    def to_prompt(self, prev_beat_last_sentence: str = "", char_snapshots: str = "",
                  rhythm_hint: str = "") -> str:
        """生成此 beat 的 writing prompt"""
        lines = [
            f"## 节拍 {self.index+1}: {self.function}",
            f"功能目标: {self.goal}",
            f"情绪弧: {self.emotion_start} → {self.emotion_end}",
            f"冲突类型: {self.conflict_type} | 强度: {self.conflict_intensity}/5",
            f"字数范围: {self.min_words}-{self.max_words}字",
        ]
        if self.paragraph_style:
            lines.append(f"段落节奏: {self.paragraph_style}")
        if rhythm_hint:
            lines.append(f"⚠️ 整章节奏提示: {rhythm_hint}")
        if self.key_event:
            lines.append(f"核心事件: {self.key_event}")
        if self.character_focus:
            lines.append(f"聚焦角色: {self.character_focus}")
        if self.hook_type:
            lines.append(f"钩子类型: {self.hook_type}")
        if prev_beat_last_sentence:
            lines.append(f"\n紧接上文末尾: 「{prev_beat_last_sentence}」")
            lines.append("必须从此句直接继续，不能跳时间、不能换场景。")
        if char_snapshots:
            lines.append(f"\n角色当前状态:\n{char_snapshots}")
        
        return "\n".join(lines)


class BeatDecomposer:
    """章节→节拍 拆解器
    
    从本章大纲中拆出 5-7 个独立节拍，分配功能/情绪/冲突/钩子。
    拆解策略：规则（确定性分布）+ 随机扰动（冲突强度/钩子类型）。
    """
    
    def __init__(self, seed: int = None):
        self.rng = random.Random(seed)
    
    def decompose(self, chapter_outline: dict, chapter_num: int,
                  is_first_chapter: bool = False, is_climax_chapter: bool = False,
                  available_characters: list = None) -> list:
        """拆解一章大纲为 beat 列表
        
        Args:
            chapter_outline: {"title","summary","emotion_curve","characters","hook",...}
            chapter_num: 章节号
            is_first_chapter: 是否为第一章（需要开幕 hook）
            is_climax_chapter: 是否为高潮章（需要高潮释放）
            available_characters: 本章出场角色名列表
        
        Returns:
            list[Beat]
        """
        summary = chapter_outline.get("summary", "")
        emotion_curve = chapter_outline.get("emotion_curve", "")
        chars = available_characters or chapter_outline.get("characters", [])
        existing_hook = chapter_outline.get("hook", "")
        
        # ── 步骤1: 选择 beat 功能序列 ──
        if is_first_chapter:
            function_sequence = self._first_chapter_sequence()
        elif is_climax_chapter:
            function_sequence = self._climax_chapter_sequence()
        else:
            function_sequence = self._standard_chapter_sequence(chapter_num)
        
        # ── 步骤2: 创建 beat 对象 ──
        beats = []
        for i, func in enumerate(function_sequence):
            beat = Beat(i, func)
            beats.append(beat)
        
        # ── 步骤3: 分配冲突类型和强度 ──
        self._assign_conflicts(beats, summary)
        
        # ── 步骤4: 分配钩子类型 ──
        hook_type = self._pick_hook_type(chapter_num, existing_hook)
        beats[-1].hook_type = hook_type
        
        # ── 步骤5: 分配核心事件到各 beat ──
        self._distribute_events(beats, summary, chars)
        
        # ── 步骤6: 随机微调 temperature ──
        for beat in beats:
            beat.temperature = round(beat.temperature + self.rng.uniform(-0.05, 0.05), 2)
            beat.temperature = max(0.7, min(1.0, beat.temperature))
        
        return beats
    
    def _first_chapter_sequence(self) -> list:
        """第一章: 开幕钩子 + 困境显形 + 金手指激活"""
        return [
            "opening_hook",
            "obstacle_build",
            "character_highlight",
            "turning_point",
            "closing_hook",
        ]
    
    def _climax_chapter_sequence(self) -> list:
        """高潮章: 障碍→冲突→转折→高潮释放→余韵→钩子"""
        return [
            "obstacle_build",
            "conflict_ignition",
            "turning_point",
            "climax_release",
            "emotion_settle",
            "closing_hook",
        ]
    
    def _standard_chapter_sequence(self, ch_num: int) -> list:
        """标准章: 多种变体轮换，防止千篇一律
        
        包含描写/反思节拍的变体，防止纯动作碎片化
        """
        variants = [
            # 变体A: 障碍→冲突→人设→转折→钩子
            ["opening_hook", "obstacle_build", "conflict_ignition", "character_highlight", "turning_point", "closing_hook"],
            # 变体B: 信息揭示→冲突→人设→转折→钩子
            ["info_reveal", "conflict_ignition", "character_highlight", "turning_point", "closing_hook"],
            # 变体C: 伏笔回收→障碍→冲突→人设→情绪沉降→钩子
            ["setup_payoff", "obstacle_build", "conflict_ignition", "character_highlight", "emotion_settle", "closing_hook"],
            # 变体D: 人设高光→障碍→转折→信息揭示→钩子
            ["character_highlight", "obstacle_build", "turning_point", "info_reveal", "closing_hook"],
        ]
        
        # 含双重呼吸的变体
        variants.append(
            ["emotion_settle", "info_reveal", "character_highlight", "obstacle_build", "conflict_ignition", "closing_hook"]
        )
        variants.append(
            ["character_highlight", "emotion_settle", "turning_point", "info_reveal", "obstacle_build", "closing_hook"]
        )
        
        # 基于章节号选择变体（确定性但看起来随机）
        return variants[ch_num % len(variants)]
    
    def _assign_conflicts(self, beats: list, summary: str):
        """分配冲突类型和强度，逐beat递增"""
        for i, beat in enumerate(beats):
            # 按功能选择合适的冲突类型
            if beat.function in ("opening_hook", "info_reveal", "setup_payoff"):
                beat.conflict_type = self.rng.choice(["IN", "EN"])
            elif beat.function in ("conflict_ignition", "climax_release"):
                beat.conflict_type = self.rng.choice(["IR", "DE"])
            elif beat.function == "character_highlight":
                beat.conflict_type = "IN"
            elif beat.function == "turning_point":
                beat.conflict_type = self.rng.choice(["IR", "EN", "DE"])
            elif beat.function == "obstacle_build":
                beat.conflict_type = self.rng.choice(["EN", "IR"])
            elif beat.function == "closing_hook":
                beat.conflict_type = self.rng.choice(["IR", "DE"])
            
            # 冲突强度：从2开始，逐beat递增到5
            base_intensity = min(5, 2 + i)
            beat.conflict_intensity = min(5, base_intensity + self.rng.choice([-1, 0, 0, 1]))
    
    def _pick_hook_type(self, ch_num: int, existing_hook: str = "") -> str:
        """选择钩子类型，避免连续重复"""
        # 基于现有大纲中的钩子描述推断类型
        if existing_hook:
            if any(w in existing_hook for w in ["突然", "猛然", "危险", "刀", "剑"]):
                return "crisis_suspense"
            if any(w in existing_hook for w in ["反转", "意外", "真相", "竟然是"]):
                return "reversal_tease"
            if any(w in existing_hook for w in ["冷笑", "不屑", "等着瞧", "走着瞧"]):
                return "face_slap_preview"
            if any(w in existing_hook for w in ["秘密", "发现", "揭晓", "竟然是"]):
                return "secret_reveal"
        
        # 否则按章号轮换
        return HOOK_TYPES[ch_num % len(HOOK_TYPES)]
    
    def _distribute_events(self, beats: list, summary: str, chars: list):
        """将本章的核心叙事分配到各 beat"""
        # 简单策略：把 summary 拆成 N 段，分配到各 beat
        # 更复杂的版本可以用 LLM 精拆，但这里是确定性基础版
        
        for beat in beats:
            # 给非 hook beat 分配一个角色聚焦
            if beat.function not in ("opening_hook", "closing_hook") and chars:
                beat.character_focus = self.rng.choice(chars)
            
            # 给每个 beat 一个概要事件描述
            beat.key_event = f"本章 {beat.goal[:20]}"


# ── 批量拆解 ──

def decompose_chapter_beats(chapter_outline: dict, chapter_num: int,
                             is_first: bool = False, is_climax: bool = False,
                             characters: list = None, seed: int = None) -> list:
    """便捷函数：拆解一章为 beat 列表"""
    decomposer = BeatDecomposer(seed=seed or chapter_num)
    return decomposer.decompose(chapter_outline, chapter_num, is_first, is_climax, characters)


# ── 测试 ──

if __name__ == "__main__":
    test_outline = {
        "title": "第5章 试探",
        "summary": "主角在宗门大比中遇到第一个强敌，试探对方实力后决定隐藏底牌",
        "emotion_curve": "紧张→对抗→暗喜→悬念",
        "characters": ["叶凡", "王腾", "柳长老"],
        "hook": "主角转身离开时，没注意到王腾眼中闪过一丝异样的光芒",
    }
    
    d = BeatDecomposer(seed=42)
    beats = d.decompose(test_outline, 5, characters=["叶凡", "王腾", "柳长老"])
    
    print(f"拆解第5章 → {len(beats)} 个节拍:")
    for b in beats:
        d = b.to_dict()
        print(f"  Beat {d['index']}: {d['function']} | "
              f"冲突={d['conflict_type']}:{d['conflict_intensity']} | "
              f"情绪={d['emotion_start']}→{d['emotion_end']} | "
              f"字数={d['min_words']}-{d['max_words']} | t={d['temperature']}")
        if d['hook_type']:
            print(f"    钩子: {d['hook_type']}")
