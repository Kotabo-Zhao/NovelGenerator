"""NovelGenerator — StoryGraph Interventions: 剧情图谱 → 主动干预 → Writer 上下文

闭环反馈系统：剧情图谱不仅是"查看"工具，更是"指导"引擎。
每章生成前，从 storygraph/arcs/calibration 数据中自动提取干预指令，
注入 Writer 上下文，让数据反哺创作。

干预类型：
- FORESHADOW_DUE: 伏笔到期提醒（必须在N章内回收）
- FORESHADOW_OVERDUE: 伏笔超期告警（立即回收）
- THREAD_STALE: 休眠剧情线唤醒（N章无进展）
- CHARACTER_ABSENT: 角色长期未出场
- TENSION_ADJUST: 紧张度异常调整
- EMOTION_BALANCE: 情绪同质化破局
- ARC_TRANSITION: 弧间过渡提示
- DOPAMINE_CHECK: 爽点饥饿提醒
"""
import json
import os
import logging
from typing import Optional

log = logging.getLogger(__name__)


class Intervention:
    """一条写作干预指令"""
    __slots__ = ("type", "severity", "instruction", "detail", "priority")
    
    def __init__(self, itype: str, severity: str, instruction: str, detail: str = "", priority: int = 0):
        self.type = itype
        self.severity = severity  # must | should | suggest
        self.instruction = instruction
        self.detail = detail
        self.priority = priority
    
    def to_injection(self) -> str:
        """生成注入 Writer 上下文的指令文本"""
        icons = {"must": "🔴", "should": "🟡", "suggest": "🟢"}
        icon = icons.get(self.severity, "⚪")
        lines = [f"{icon} 【{self.type}】{self.instruction}"]
        if self.detail:
            lines.append(f"   详情: {self.detail}")
        return "\n".join(lines)


class StoryGraphInterventions:
    """剧情图谱干预器 — 每章生成前自动运行"""
    
    def __init__(self):
        self.interventions = []
    
    def analyze(self, storygraph: dict, arcs: dict = None, calibration: dict = None,
                chapter_num: int = 0, completed_chapters: list = None,
                chapter_outline: dict = None) -> list:
        """分析剧情图谱数据，生成干预指令列表
        
        Args:
            storygraph: storygraph.json 数据
            arcs: arcplans.json 数据
            calibration: 最新校准报告
            chapter_num: 当前要生成的章节号
            completed_chapters: 已完成的章节号列表
            chapter_outline: 本章大纲
        
        Returns:
            list[Intervention]
        """
        self.interventions = []
        completed = completed_chapters or []
        total_chapters = max(chapter_num, max(completed) if completed else chapter_num)
        
        # ── 1: 伏笔到期检测 ──
        self._check_foreshadows(storygraph, chapter_num)
        
        # ── 2: 休眠剧情线 ──
        self._check_stale_threads(storygraph, chapter_num)
        
        # ── 3: 角色长期未出场 ──
        self._check_absent_characters(storygraph, chapter_num, chapter_outline)
        
        # ── 4: 情绪同质化 ──
        self._check_emotion_monotony(storygraph, chapter_num)
        
        # ── 5: 紧张度异常 ──
        self._check_tension(storygraph, chapter_num)
        
        # ── 6: 弧间过渡 ──
        if arcs:
            self._check_arc_transition(arcs, chapter_num)
        
        # ── 7: 爽点饥饿 ──
        if completed:
            self._check_dopamine_gap(completed, chapter_num)
        
        # 按优先级排序（must > should > suggest）
        severity_order = {"must": 0, "should": 1, "suggest": 2}
        self.interventions.sort(key=lambda x: (severity_order.get(x.severity, 3), -x.priority))
        
        return self.interventions
    
    def to_context_block(self) -> str:
        """将所有干预指令合并为注入 Writer 上下文的文本块"""
        if not self.interventions:
            return ""
        
        musts = [i for i in self.interventions if i.severity == "must"]
        shoulds = [i for i in self.interventions if i.severity == "should"]
        suggests = [i for i in self.interventions if i.severity == "suggest"]
        
        parts = ["\n## 🎯 剧情图谱干预指令（请在写作时参考）\n"]
        
        if musts:
            parts.append("### 必须执行")
            for i in musts:
                parts.append(i.to_injection())
            parts.append("")
        
        if shoulds:
            parts.append("### 建议执行")
            for i in shoulds:
                parts.append(i.to_injection())
            parts.append("")
        
        if suggests:
            parts.append("### 可选优化")
            for i in suggests:
                parts.append(i.to_injection())
            parts.append("")
        
        parts.append("---\n注意：以上指令基于剧情图谱实时分析生成，请在不破坏本章大纲的前提下尽量遵循。\n")
        return "\n".join(parts)
    
    # ── 各检测器 ──
    
    def _check_foreshadows(self, storygraph: dict, ch: int):
        """伏笔检测"""
        ledger = storygraph.get("foreshadow_ledger", {})
        if not ledger:
            return
        
        due_soon = []
        overdue = []
        
        for fid, fs in ledger.items():
            if fs.get("status") not in ("planted", "hinted"):
                continue
            planned = fs.get("planned_payoff_chapter", 999)
            gap = planned - ch
            
            if gap < 0:
                overdue.append((fid, fs, abs(gap)))
            elif gap <= 3:
                due_soon.append((fid, fs, gap))
        
        for fid, fs, n in overdue[:3]:
            self.interventions.append(Intervention(
                "FORESHADOW_OVERDUE", "must",
                f"伏笔「{fs.get('description','')[:30]}」已超期{n}章，必须在本章或下章回收",
                f"埋设于Ch{fs.get('planted_chapter','?')}，计划Ch{fs.get('planned_payoff_chapter','?')}回收，重要度P{fs.get('importance',3)}",
                priority=fs.get("importance", 3) * 2
            ))
        
        for fid, fs, gap in due_soon[:2]:
            self.interventions.append(Intervention(
                "FORESHADOW_DUE", "should",
                f"伏笔「{fs.get('description','')[:30]}」将在{gap}章后到期，请准备回收",
                f"埋设于Ch{fs.get('planted_chapter','?')}，计划Ch{fs.get('planned_payoff_chapter','?')}",
                priority=fs.get("importance", 3)
            ))
    
    def _check_stale_threads(self, storygraph: dict, ch: int):
        """休眠剧情线检测"""
        threads = storygraph.get("plot_threads", {})
        for tid, t in threads.items():
            if t.get("status") not in ("active", "advancing"):
                continue
            if t.get("priority", 0) < 3:
                continue
            
            nodes = t.get("key_nodes", [])
            if not nodes:
                continue
            
            last_ch = nodes[-1].get("chapter", 0)
            gap = ch - last_ch
            
            if gap >= 8 and t.get("priority", 0) >= 4:
                self.interventions.append(Intervention(
                    "THREAD_STALE", "should",
                    f"高优剧情线「{t.get('name','?')}」已连续{gap}章无进展，建议本章提及或推进",
                    f"优先级P{t.get('priority',3)}，当前状态{t.get('status','?')}",
                    priority=t.get("priority", 3)
                ))
            elif gap >= 5:
                self.interventions.append(Intervention(
                    "THREAD_STALE", "suggest",
                    f"剧情线「{t.get('name','?')}」已{gap}章未推进，可考虑提及",
                    f"优先级P{t.get('priority',3)}",
                    priority=t.get("priority", 3) - 1
                ))
    
    def _check_absent_characters(self, storygraph: dict, ch: int, outline: dict = None):
        """角色出场检测"""
        snaps = storygraph.get("char_snapshots", {})
        if not snaps:
            return
        
        # 获取大纲中本章出场的角色
        outline_chars = set(outline.get("characters", [])) if outline else set()
        
        for name, snap in snaps.items():
            last = snap.get("last_chapter_appeared", 0)
            if last == 0:
                continue
            gap = ch - last
            
            # 如果有未完成的目标但很久没出现
            goals = snap.get("active_goals", [])
            if gap >= 6 and goals:
                self.interventions.append(Intervention(
                    "CHARACTER_ABSENT", "should",
                    f"角色「{name}」已连续{gap}章未出场，且有未完成目标: {', '.join(goals[:2])}",
                    f"上次出场Ch{last}，当前情绪{snap.get('current_emotion','?')}",
                    priority=3
                ))
            elif gap >= 4 and name not in outline_chars:
                self.interventions.append(Intervention(
                    "CHARACTER_ABSENT", "suggest",
                    f"角色「{name}」已{gap}章未出场，可考虑安排出场",
                    f"上次出场Ch{last}",
                    priority=1
                ))
    
    def _check_emotion_monotony(self, storygraph: dict, ch: int):
        """情绪同质化检测（基于剧情线紧张度变化）"""
        threads = storygraph.get("plot_threads", {})
        tensions = [t.get("current_tension", 5) for t in threads.values()
                    if t.get("status") in ("active", "advancing")]
        
        if len(tensions) >= 3:
            avg = sum(tensions) / len(tensions)
            # 所有活跃线程紧张度都很低 → 需要升温
            if avg <= 3:
                self.interventions.append(Intervention(
                    "EMOTION_BALANCE", "should",
                    f"当前所有活跃剧情线平均紧张度仅{avg:.1f}/10，建议本章升温——增加冲突或揭示新信息",
                    f"活跃线{len(tensions)}条",
                    priority=3
                ))
            # 所有都很高 → 需要缓冲
            elif avg >= 8:
                self.interventions.append(Intervention(
                    "EMOTION_BALANCE", "suggest",
                    f"当前紧张度偏高({avg:.1f}/10)，建议本章加入喘息空间——日常/温情/幽默片段",
                    f"连续高压可能让读者疲劳",
                    priority=2
                ))
    
    def _check_tension(self, storygraph: dict, ch: int):
        """紧张度曲线检查（从 causal_links 推断）"""
        links = storygraph.get("causal_links", [])
        pending = [l for l in links if l.get("status") == "pending"]
        
        if len(pending) >= 5:
            self.interventions.append(Intervention(
                "TENSION_ADJUST", "suggest",
                f"有{len(pending)}条因果链尚未兑现，可能导致读者困惑。建议本章回收1-2条",
                f"最早的因果链来自Ch{min(l.get('cause_chapter',ch) for l in pending)}",
                priority=2
            ))
    
    def _check_arc_transition(self, arcs: dict, ch: int):
        """弧过渡检测"""
        arc_list = arcs.get("arcs", [])
        if not arc_list:
            return
        
        current_arc = arcs.get("current_arc")
        if not current_arc:
            return
        
        # 检查是否接近弧的结尾
        total = current_arc.get("total_in_arc", 0)
        pos = current_arc.get("current_position", 0)
        
        if total > 0 and pos >= total - 1:
            # 即将进入下一弧
            self.interventions.append(Intervention(
                "ARC_TRANSITION", "should",
                f"即将完成「{current_arc.get('label','')}」，下章将进入新弧。请在本章做好弧间过渡：收束当前线、埋下新线种",
                f"当前弧类型: {current_arc.get('type','')}，目标: {current_arc.get('goal','')[:40]}",
                priority=4
            ))
        
        # 高潮弧
        if current_arc.get("type") == "climax":
            self.interventions.append(Intervention(
                "ARC_TRANSITION", "must",
                f"当前处于高潮弧「{current_arc.get('label','')}」，本章必须提升写作强度：句式缩短、节奏加快、情绪推向顶点",
                f"弧目标: {current_arc.get('goal','')[:50]}",
                priority=5
            ))
    
    def _check_dopamine_gap(self, completed: list, ch: int):
        """爽点间距检测"""
        # 检查最近5章的距离
        recent = [c for c in completed if c >= ch - 5 and c < ch]
        if len(recent) >= 4:
            self.interventions.append(Intervention(
                "DOPAMINE_CHECK", "suggest",
                f"近{len(recent)}章连续无间断，建议本章安排一个明确的爽点——小打脸/小升级/或小发糖",
                f"避免连续平淡导致读者流失",
                priority=1
            ))


# ── 字符活人感增强 ──

# v2.10: 感官细节改为原则性指引（不给示例模板，防止LLM抄袭重复短语）
SENSORY_DETAILS = {
    "visual": [
        '加入一个本章场景独有的视觉细节——不是「大殿很宏伟」，而是你只有在这个场景里才能看到的东西',
        '光线: 此刻的光从哪来？什么颜色？照在什么东西上产生了什么效果？',
        '近距离: 角色此刻能看到的最小细节是什么？指甲/袖口/桌面纹理？',
    ],
    "auditory": [
        '加入一个本章场景独有的声音——不是「风声」，是这个时刻、这个地点才有的声音',
        '远景声音: 远处有什么在响？把这声音写进来制造空间感',
        '沉默的声音: 此刻最不该响的东西是什么？让它响。',
    ],
    "tactile": [
        '加入一个触觉细节: 角色此刻的身体接触到什么？温度？质地？',
        '温度变化: 角色的皮肤感受到了什么温度变化？',
    ],
    "olfactory": [
        '加入一个气味: 这个场景的独特味道是什么？',
        '气味对比: 两种气味同时存在，一种盖过另一种，暗示什么？',
    ],
    "inner": [
        '用一个具体的身体感受来表达角色的内心状态（不要写器官名称，写动作或行为变化）',
        '让角色的身体做一件他自己都没意识到的事',
    ],
}

# 不完美行为 — 原则替代模板
IMPERFECTION_BEATS = [
    "角色做一个动作，但做歪了或做过头了——这种微小的失控比任何心理描写都真实",
    "角色说了一句不该说的话，或者一句该说的话他没说——语言上的不完美",
    "角色产生了一个念头，但马上又否定了自己——思维的不完美",
    "角色注意到自己的某个细节（衣服皱了、手在抖），试图掩饰但没成功",
    "两个角色之间的节奏错位：A以为B要说什么，结果B说的是别的",
]

# 对话质感提示
DIALOGUE_HUMAN_TIPS = [
    "对话中安排一次打断——角色A话没说完，B就插嘴",
    "安排一次答非所问——角色故意回避问题",
    '加入一句毫无意义的日常对话（「吃了吗」「嗯」）',
    "一个人说话时，另一个人在看别处——注意力不在对话上",
    "对话中出现一次沉默——不急着填满",
]


def get_sensory_injection(senses: list = None) -> str:
    """随机返回一条感官细节注入提示"""
    import random
    if not senses:
        senses = random.sample(list(SENSORY_DETAILS.keys()), 2)
    injections = []
    for s in senses:
        if s in SENSORY_DETAILS:
            injections.append(random.choice(SENSORY_DETAILS[s]))
    return "【活人感】请在合适位置加入这些感官细节: " + " / ".join(injections)


def get_imperfection_hint() -> str:
    """随机返回一条不完美行为提示"""
    import random
    return "【活人感】" + random.choice(IMPERFECTION_BEATS)


def get_dialogue_hint() -> str:
    """随机返回一条对话质感提示"""
    import random
    return "【活人感·对话】" + random.choice(DIALOGUE_HUMAN_TIPS)


def get_humanness_injections(count: int = 3) -> list:
    """批量生成活人感注入指令"""
    import random
    hints = []
    # 至少1条感官
    hints.append(get_sensory_injection())
    # 50%概率加一条不完美行为
    if random.random() < 0.5:
        hints.append(get_imperfection_hint())
    # 如果有对话场景，加对话提示
    if random.random() < 0.4:
        hints.append(get_dialogue_hint())
    # 补足到count条
    while len(hints) < count:
        hints.append(get_sensory_injection(random.sample(list(SENSORY_DETAILS.keys()), 1)))
    return hints[:count]


# ── 字符口癖库 ──

CHARACTER_VOICE_BANK = {
    # 通用口癖模板，通过角色性格自动匹配
    "傲娇": {
        "phrases": ["哼。", "谁、谁关心你了。", "随便你。", "我才不是……算了。"],
        "pattern": "嘴上否定，行动上关心。说反话。说话时转过脸去。",
    },
    "冷静": {
        "phrases": ["有意思。", "不急。", "再等等。", "你说得对。"],
        "pattern": "话少。每个字都有分量。不抢话。回答前有停顿。",
    },
    "热血": {
        "phrases": ["干就完了！", "怕什么！", "这算什么！", "来啊！"],
        "pattern": "短句。感叹号。冲在前面。说话不过脑。",
    },
    "阴郁": {
        "phrases": ["……", "随你。", "没区别。", "你走吧。"],
        "pattern": "话少且低沉。看地面不看人。笑的时候嘴角不动。",
    },
    "狡黠": {
        "phrases": ["你猜。", "不告诉你。", "有条件的哦。", "成交。"],
        "pattern": "说话绕弯。每句话都在算。笑的时候眼睛眯起来。",
    },
    "温柔": {
        "phrases": ["没事的。", "我在。", "慢慢来。", "你辛苦了。"],
        "pattern": "轻声。不打断别人。会用反问代替命令。",
    },
}

def get_character_voice_guide(character_name: str, personality: str = "") -> str:
    """获取角色口癖指导"""
    import random
    if not personality:
        return ""
    
    # 模糊匹配性格关键词
    best_match = None
    for key, data in CHARACTER_VOICE_BANK.items():
        if key in personality:
            best_match = data
            break
    
    if not best_match:
        return ""
    
    phrase = random.choice(best_match["phrases"])
    return (f"【角色口癖·{character_name}】性格特征: {best_match['pattern']} "
            f"标志性表达示例: 「{phrase}」")


# ── 便捷函数 ──

def analyze_and_inject(novel_dir: str, chapter_num: int, chapter_outline: dict = None) -> str:
    """一站式：分析剧情图谱 → 生成干预指令 + 活人感注入 → 返回 Writer 上下文块"""
    import os, json
    from .atomic_io import safe_read_json
    
    context_blocks = []
    
    # 1. 剧情图谱干预
    sg_path = os.path.join(novel_dir, "storygraph.json")
    arc_path = os.path.join(novel_dir, "arcplans.json")
    
    if os.path.exists(sg_path):
        sg_data = safe_read_json(sg_path)
        arcs_data = safe_read_json(arc_path) if os.path.exists(arc_path) else None
        
        interventions = StoryGraphInterventions()
        interventions.analyze(sg_data, arcs_data, None, chapter_num)
        
        block = interventions.to_context_block()
        if block:
            context_blocks.append(block)
    
    # 2. 活人感注入
    import random
    humanness = get_humanness_injections(3)
    if humanness:
        parts = ["\n## 🫀 活人感增强指令\n"]
        for h in humanness:
            parts.append(f"- {h}")
        parts.append("")
        context_blocks.append("\n".join(parts))
    
    # 3. 角色口癖
    if chapter_outline and chapter_outline.get("characters"):
        voice_parts = []
        for char_name in chapter_outline["characters"][:2]:
            # 尝试获取角色性格（从 plan 中获取，这里简化）
            guide = get_character_voice_guide(char_name, "")
            if guide:
                voice_parts.append(guide)
        if voice_parts:
            context_blocks.append("\n## 🗣️ 角色口癖指导\n" + "\n".join(f"- {v}" for v in voice_parts) + "\n")
    
    return "\n".join(context_blocks)


# ── 测试 ──

if __name__ == "__main__":
    # 模拟 storygraph 数据
    mock_sg = {
        "plot_threads": {
            "t1": {"name": "复仇主线", "type": "main_plot", "status": "advancing", 
                   "priority": 5, "current_tension": 8, "key_nodes": [
                       {"chapter": 1, "event": "门派被灭", "tension": 8},
                       {"chapter": 3, "event": "找到线索", "tension": 7},
                   ]},
            "t2": {"name": "感情线", "type": "subplot", "status": "active",
                   "priority": 2, "current_tension": 3, "key_nodes": [
                       {"chapter": 2, "event": "初次相遇", "tension": 3},
                   ]},
        },
        "foreshadow_ledger": {
            "f1": {"description": "师父留下的玉佩有古怪", "planted_chapter": 1,
                   "planned_payoff_chapter": 10, "status": "hinted", "importance": 4,
                   "hint_count": 2, "last_hint_chapter": 5},
            "f2": {"description": "大师兄的身份秘密", "planted_chapter": 3,
                   "planned_payoff_chapter": 8, "status": "planted", "importance": 3,
                   "hint_count": 1, "last_hint_chapter": 3},
        },
        "char_snapshots": {
            "柳师妹": {"last_chapter_appeared": 2, "current_emotion": "担忧",
                       "active_goals": ["找到师兄"], "current_location": "青州城"},
            "反派": {"last_chapter_appeared": 5, "current_emotion": "愤怒",
                     "active_goals": ["夺取玉佩"]},
        },
        "causal_links": [
            {"cause_chapter": 1, "cause_event": "门派被灭", "effect_chapter": 7, 
             "effect_event": "发现真凶", "status": "pending"},
            {"cause_chapter": 3, "cause_event": "得到玉佩", "effect_chapter": 7,
             "effect_event": "触发机关", "status": "pending"},
        ],
    }
    
    mock_arcs = {
        "arcs": [{
            "arc_id": 1, "label": "建置弧", "type": "setup",
            "chapters": [1,2,3,4], "start_chapter": 1, "end_chapter": 4,
            "goal": "建立世界观，埋下伏笔",
        }, {
            "arc_id": 2, "label": "对抗弧", "type": "rising",
            "chapters": [5,6,7,8,9,10], "start_chapter": 5, "end_chapter": 10,
            "goal": "冲突升级，中点转折",
        }],
        "current_arc": {
            "arc_id": 2, "label": "对抗弧", "type": "rising",
            "current_position": 3, "total_in_arc": 6,
            "start_chapter": 5, "end_chapter": 10,
            "goal": "冲突升级，中点转折",
        },
        "current_chapter": 7,
    }
    
    interventions = StoryGraphInterventions()
    result = interventions.analyze(mock_sg, mock_arcs, None, 7, [1,2,3,4,5,6])
    
    print(f"检测到 {len(result)} 条干预指令:\n")
    for inv in result:
        print(inv.to_injection())
        print()
    
    print("=== Writer 上下文块 ===")
    print(interventions.to_context_block())
    
    print("=== 活人感注入 ===")
    for hint in get_humanness_injections(3):
        print(hint)
