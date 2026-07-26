"""NovelGenerator — CreativeSeedEngine: 架构级创意随机性注入

核心理念：不是让 LLM 自己「想一个有趣的点子」，而是给 LLM
一套随机抽取的创意约束，强迫它在一个框架内发挥。这比「写点
有创意的」这种模糊指令有效得多。

架构位置：Planner 规划前注入 → 成为 MUST_INCLUDE 级别约束
"""

import random
import json
import os
import hashlib
from typing import List, Dict, Optional
import logging

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════
# 创意种子池
# ═══════════════════════════════════════════

# 1. 金手指种子 — 拒绝「系统+面板」套路
CHEAT_SEEDS = [
    # 知识降维型
    {"id": "statistics_cheat", "category": "金手指", "type": "知识降维",
     "seed": "主角用统计学/概率论分析修仙世界——灵气分布是正态分布，突破境界需要置信区间达到95%",
     "constraint": "金手指必须是主角原有的专业知识，不能是系统赐予的超自然力量",
     "why": "区别于「捡到一个系统」的套路，读者的智力参与感更强"},

    {"id": "thermo_cheat", "category": "金手指", "type": "知识降维",
     "seed": "主角用热力学/工程学重构力量体系——炼气是熵减过程，突破需要最小化自由能",
     "constraint": "力量的代价必须用学科公式量化，不能靠「消耗寿命/灵力」这种模糊描述"},

    {"id": "game_theory_cheat", "category": "金手指", "type": "知识降维",
     "seed": "主角用博弈论在修真界政治中翻云覆雨——每个宗门的策略都是博弈矩阵的一格",
     "constraint": "必须展现主角的计算过程，读者能跟着推理"},

    {"id": "forensic_cheat", "category": "金手指", "type": "知识降维",
     "seed": "主角是法医/刑侦出身，在修仙界用解剖学+痕迹学破案——修士的死亡也能被尸检",
     "constraint": "关键线索必须通过解剖/检验过程展现，不能靠「灵识一扫就知道了」"},

    # 代价型
    {"id": "memory_cost", "category": "金手指", "type": "代价型",
     "seed": "主角每次使用能力都会失去一段记忆——可能是技能、可能是某个人、可能是自己是谁",
     "constraint": "每章必须明确展示一次「失去」的具体后果",
     "why": "对比「无限开挂」的零代价金手指，读者会关心主角的每次选择"},

    {"id": "time_cost", "category": "金手指", "type": "代价型",
     "seed": "主角的能力消耗的不是灵力而是「存在时间」——用一次少活一天，最终寿命是可量化的",
     "constraint": "必须在第一章就明确告知读者剩余天数，之后每用一次能力就扣减"},

    {"id": "identity_cost", "category": "金手指", "type": "代价型",
     "seed": "主角每次突破都会失去一种情感——越强越不像人，最后要面对「我还是我吗」的存在危机",
     "constraint": "失去的情感必须通过「他人视角」来表现（如爱人发现主角眼神空洞）"},

    # 误解型（迪化流）
    {"id": "misunderstood_genius", "category": "金手指", "type": "误解型",
     "seed": "主角只想躺平摸鱼，但所有巧合都被外界解读为深谋远虑的大佬布局",
     "constraint": "主角的真实意图与外界解读必须有明确反差展示，不能模糊处理"},

    {"id": "fake_expert", "category": "金手指", "type": "误解型",
     "seed": "主角是个江湖骗子，靠忽悠混进正道宗门，结果每次忽悠都歪打正着变成了预言",
     "constraint": "必须展现主角骗局差点穿帮的紧张时刻"},

    # 反套路型
    {"id": "bug_cheat", "category": "金手指", "type": "反套路型",
     "seed": "主角发现了世界的「bug」——比如时间在子时三刻会倒退一秒、第四面墙偶尔有裂缝",
     "constraint": "bug必须遵循自恰的底层逻辑，不能随心所欲"},

    {"id": "anti_system", "category": "金手指", "type": "反套路型",
     "seed": "主角得到一个「反系统」——别人的系统面板在他眼前都是可修改的文本框，他可以篡改别人的数据",
     "constraint": "篡改必须有反噬/被发现的风险，不能无限改"},

    {"id": "trader_cheat", "category": "金手指", "type": "反套路型",
     "seed": "主角的能力是「交易」而非「修炼」——用已有的东西等价交换未有的东西，修炼是商科而非武学",
     "constraint": "每笔交易必须有明确的代价和逻辑，不能空手套白狼"},

    # 轮回/信息差型
    {"id": "hundred_lives", "category": "金手指", "type": "轮回型",
     "seed": "主角轮回百世积累的不是力量而是「信息」——知道每个人的秘密、每条隐藏的任务线",
     "constraint": "每次使用前世记忆必须注明是第几世获取的，体现积累感"},

    {"id": "save_load", "category": "金手指", "type": "轮回型",
     "seed": "主角的能力类似游戏存档——可以读档重来，但存档位有限（比如只有3个），每个选择都有不可逆的代价",
     "constraint": "存档选择必须是真正的两难，读者应该能理解为什么选A不选B也会后悔"},
]

# 2. 角色关系种子 — 拒绝「主角+工具人+反派」三角
CHARACTER_SEEDS = [
    {"id": "mentor_villain", "category": "人物关系",
     "seed": "主角的导师就是最终反派——不是「黑化」，而是从一开始就在利用主角达成自己的目的",
     "constraint": "揭露时必须有一个让读者回看前文会发现伏笔的具体细节"},

    {"id": "ally_betrayal_double", "category": "人物关系",
     "seed": "最忠实的盟友正在背叛主角——但背叛的原因是出于爱/保护，不是利益",
     "constraint": "揭露后必须有至少一章从该角色的视角回溯，让读者理解动机"},

    {"id": "enemy_dependence", "category": "人物关系",
     "seed": "主角必须依靠最大的敌人才能活下去——共享生命/共用一个力量源头/互相是对方的解药",
     "constraint": "每次互动必须同时展现「杀意」和「不得不合作」的张力"},

    {"id": "family_antagonist", "category": "人物关系",
     "seed": "主角最亲近的家人（父母/配偶/子女）是这个世界规则的执行者/维护者",
     "constraint": "必须有至少一次「亲情 vs 正义/生存」的真实挣扎场景"},

    {"id": "identity_copy", "category": "人物关系",
     "seed": "存在一个与主角一模一样的人——可能是分身/克隆/平行世界，但做出了主角最害怕的选择",
     "constraint": "两者的对比必须在至少两个关键决策点上展现"},

    {"id": "collective_protagonist", "category": "人物关系",
     "seed": "没有单一主角——叙事主体是一个家族/团队的集体视角，每章/每卷从不同角色视角展开",
     "constraint": "每个视角角色的动机和知识边界必须不同（不允许全知视角）"},

    {"id": "ai_companion", "category": "人物关系",
     "seed": "主角有一个不按套路出牌的AI/精灵/系统助手——但这个助手有自己的议程，不完全服从主角",
     "constraint": "助手的行为必须有自恰的逻辑（不是任性，是有原因的不同意）"},
]

# 3. 剧情约束种子 — 强迫打破类型惯性
PLOT_CONSTRAINTS = [
    {"id": "major_death_early", "category": "剧情约束",
     "seed": "在故事前1/4处，一个看似不可替代的角色（导师/爱人/搭档）必须死亡",
     "constraint": "死亡不能是动机工具（不能是「为了激发主角」），必须是世界规则的必然结果",
     "why": "打破「重要角色不会死」的安全感"},

    {"id": "protagonist_defeat", "category": "剧情约束",
     "seed": "主角在故事的中点必须遭遇一次彻底的失败——不是假失败然后反转，是真正的、无法挽回的失败",
     "constraint": "失败后不能用金手指快速翻盘，必须用至少3章展现从零重建的过程"},

    {"id": "moral_gray", "category": "剧情约束",
     "seed": "没有绝对的对错——主角和反派的目标在某种程度上都是合理的，差别在于手段",
     "constraint": "反派必须有至少一次让读者「我觉得他说得也有道理」的场景"},

    {"id": "unreliable_narrator", "category": "剧情约束",
     "seed": "叙事视角不是客观的——读者看到的「事实」中至少有一个是主角的误解/幻觉/自欺",
     "constraint": "真相揭露时必须有可追溯的线索（不能是「突然发现一切都是梦」）"},

    {"id": "reverse_isekai", "category": "剧情约束",
     "seed": "不是现代人穿越到异世界——而是异世界的人/规则/存在「入侵」现代都市",
     "constraint": "异世界元素必须与现代社会的规则产生不兼容冲突（不是各安一隅）"},

    {"id": "no_power_fantasy", "category": "剧情约束",
     "seed": "主角的力量增长不能解决核心矛盾——力量越大，选择的困境越深",
     "constraint": "至少有三个场景：主角明明有碾压的实力，却因为非力量的原因无法使用"},

    {"id": "clock_ticking", "category": "剧情约束",
     "seed": "整个故事有一个明确的时间上限——比如主角只有90天可活，或三个月后世界大战",
     "constraint": "每章必须提及时间进度，让读者感受到倒计时的压迫"},

    {"id": "secrets_between_allies", "category": "剧情约束",
     "seed": "主角团队中的每个人都有自己的秘密——这些秘密会互相碰撞，产生连锁反应",
     "constraint": "每个秘密必须在揭露前埋至少2处伏笔"},

    {"id": "world_ending_isnt_ending", "category": "剧情约束",
     "seed": "世界观层面的重大事件（王朝覆灭/天灾/异界入侵）不是高潮而是背景——故事焦点始终在个人命运上",
     "constraint": "重大事件必须通过「一个小角色的视角」来展现，不能切换到「上帝视角」全景描述"},
]

# 4. 跨类型融合种子
FUSION_SEEDS = [
    {"id": "cultivation_folk_horror", "category": "跨类型融合",
     "combo": "修仙 + 民俗悬疑",
     "seed": "不是打怪升级的修仙，而是在湘西、苗疆、东北出马仙这些真实民俗基础上的诡异修仙",
     "constraint": "所有民俗元素必须有至少一处可考证的真实来源"},

    {"id": "history_finance", "category": "跨类型融合",
     "combo": "历史 + 商战",
     "seed": "不是帝王将相，主角是在历史夹缝中用金融手段翻云覆雨的商人——如清朝的山西票号、大航海时代的香料期货",
     "constraint": "每一笔交易必须有真实的历史经济背景作为支撑"},

    {"id": "sci_fi_bureaucracy", "category": "跨类型融合",
     "combo": "科幻 + 公务员/体制内",
     "seed": "外星文明降临后，人类组建了「星际事务管理办公室」——主角是基层公务员，日常是处理星际贸易纠纷和外星人签证",
     "constraint": "体制内的荒诞感必须通过具体细节展现（表格、流程、层层审批）"},

    {"id": "xianxia_lawsuit", "category": "跨类型融合",
     "combo": "仙侠 + 律政",
     "seed": "修仙界有「天道法则」→ 自然衍生出「天道法典」和「修士律师」——主角是修真界的法律工作者",
     "constraint": "每个案件必须基于修仙世界观的合理性（如「夺舍算不算谋杀」）"},

    {"id": "modern_wuxia_delivery", "category": "跨类型融合",
     "combo": "都市 + 武侠",
     "seed": "21世纪的快递员/外卖员群体中隐藏着最后一批武林中人——外卖箱里装的是江湖",
     "constraint": "武侠元素必须融入现代职业的日常细节"},

    {"id": "apocalypse_management", "category": "跨类型融合",
     "combo": "末日 + 经营建设",
     "seed": "不是末日求生——主角在末日开了个便利店/庇护所，靠管理和经营在废墟中建立秩序",
     "constraint": "经营数据必须可量化（每日收入/物资存量/人口变化）"},

    {"id": "mythology_startup", "category": "跨类型融合",
     "combo": "神话 + 创业",
     "seed": "天庭/奥林匹斯/北欧神殿都变成了「神界集团」——主角是刚入职的实习生，面对神界996和宫斗",
     "constraint": "必须用现代企业逻辑重新解释神话中的经典事件"},

    {"id": "detective_wuxia", "category": "跨类型融合",
     "combo": "武侠 + 悬疑/刑侦",
     "seed": "江湖中发生连环凶案，死者都是武林高手，死法各不相同但都指向同一个武功——主角是江湖唯一的验尸官",
     "constraint": "每起案件必须有独立的推理过程和物证链"},
]

# ═══════════════════════════════════════════
# 所有种子池
# ═══════════════════════════════════════════

ALL_POOLS = {
    "cheat": CHEAT_SEEDS,
    "character": CHARACTER_SEEDS,
    "plot": PLOT_CONSTRAINTS,
    "fusion": FUSION_SEEDS,
}


class CreativeSeedEngine:
    """创意种子引擎 — 随机抽取 + 去重 + 格式化注入
    
    使用方式:
        engine = CreativeSeedEngine(storage_dir)
        plan = engine.inject_creative_seeds(novel_id, genre, plan)
    """
    
    def __init__(self, storage_dir: str):
        self.storage_dir = storage_dir
        # 全局去重：已用过的种子ID不重复（跨小说）
        self._used_ids = self._load_usage()
    
    def _usage_path(self) -> str:
        return os.path.join(self.storage_dir, "_creative_seed_usage.json")
    
    def _load_usage(self) -> dict:
        try:
            with open(self._usage_path(), 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, IOError):
            return {"used_global": [], "novel_seeds": {}}
    
    def _save_usage(self):
        try:
            os.makedirs(os.path.dirname(self._usage_path()), exist_ok=True)
            with open(self._usage_path(), 'w', encoding='utf-8') as f:
                json.dump(self._used_ids, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.warning(f"Failed to save seed usage: {e}")
    
    def draw_seeds(self, novel_id: str, genre: str = "", count: int = 5) -> List[Dict]:
        """随机抽取创意种子
        
        Args:
            novel_id: 小说ID，用于记录已用种子避免重复
            genre: 小说类型，用于匹配相关种子
            count: 抽取数量
        
        Returns:
            抽取的种子列表
        """
        available = []
        
        # 每类至少抽1个（保证多样性）
        # 金手指: 2个（核心差异化）
        cheat_pool = [s for s in CHEAT_SEEDS if s["id"] not in self._used_ids["used_global"]]
        if cheat_pool:
            available.extend(random.sample(cheat_pool, min(2, len(cheat_pool))))
        
        # 人物关系: 1个
        char_pool = [s for s in CHARACTER_SEEDS if s["id"] not in self._used_ids["used_global"]]
        if char_pool:
            available.append(random.choice(char_pool))
        
        # 剧情约束: 1个
        plot_pool = [s for s in PLOT_CONSTRAINTS if s["id"] not in self._used_ids["used_global"]]
        if plot_pool:
            available.append(random.choice(plot_pool))
        
        # 跨类型融合: 1个（如果pool够大）
        fusion_pool = [s for s in FUSION_SEEDS if s["id"] not in self._used_ids["used_global"]]
        if fusion_pool and len(available) < count:
            available.append(random.choice(fusion_pool))
        
        # 如果还不够，从任意池补
        if len(available) < count:
            all_remaining = [s for s in CHEAT_SEEDS + CHARACTER_SEEDS + PLOT_CONSTRAINTS + FUSION_SEEDS
                           if s["id"] not in self._used_ids["used_global"]
                           and s not in available]
            needed = count - len(available)
            if all_remaining:
                available.extend(random.sample(all_remaining, min(needed, len(all_remaining))))
        
        # 记录已用
        for seed in available:
            self._used_ids["used_global"].append(seed["id"])
        self._used_ids["novel_seeds"][novel_id] = {
            "seeds": [s["id"] for s in available],
            "genre": genre,
        }
        
        # 限制已用列表大小（保留最近200个）
        self._used_ids["used_global"] = self._used_ids["used_global"][-200:]
        
        self._save_usage()
        return available
    
    def format_seeds_for_planner(self, seeds: List[Dict]) -> str:
        """将创意种子格式化为 Planner 可理解的 MUST_INCLUDE 约束"""
        if not seeds:
            return ""
        
        parts = ["## 🎲 创意约束（架构级强制注入——必须完全遵守）"]
        parts.append("以下约束由创意种子引擎随机抽取，你必须在大纲中完全执行。这些不是建议，是硬性要求。\n")
        
        for i, seed in enumerate(seeds):
            parts.append(f"### 约束 {i+1}: {seed.get('category', '')}「{seed.get('seed', '')[:30]}...」")
            parts.append(f"**种子描述**: {seed['seed']}")
            parts.append(f"**硬性约束**: {seed['constraint']}")
            if seed.get('why'):
                parts.append(f"**为什么有趣**: {seed['why']}")
            parts.append("")
        
        parts.append("⚠️ 这些约束与大纲的所有其他要求具有同等优先级。执行这些约束是你的核心任务。\n")
        
        return "\n".join(parts)
    
    def inject_into_planning_context(self, novel_id: str, genre: str, existing_context: str) -> tuple:
        """注入创意种子到规划上下文
        
        Returns:
            (enriched_context: str, seeds: List[Dict])
        """
        seeds = self.draw_seeds(novel_id, genre)
        seed_text = self.format_seeds_for_planner(seeds)
        
        if seed_text:
            # 种子注入到大纲规划的最前面（优先级最高）
            enriched = seed_text + "\n\n" + existing_context
            log.info(f"CreativeSeedEngine: injected {len(seeds)} seeds for novel '{novel_id}' ({genre})")
            for s in seeds:
                log.info(f"  - {s['id']}: {s['seed'][:50]}...")
            return enriched, seeds
        
        return existing_context, []
    
    def get_seed_summary(self, novel_id: str) -> Optional[str]:
        """获取某小说的创意种子汇总（给前端展示用）"""
        info = self._used_ids["novel_seeds"].get(novel_id)
        if not info:
            return None
        
        seed_ids = info["seeds"]
        summary_parts = []
        
        for sid in seed_ids:
            for pool in ALL_POOLS.values():
                for seed in pool:
                    if seed["id"] == sid:
                        summary_parts.append(f"[{seed.get('category', '')}] {seed['seed'][:60]}...")
                        break
        
        return "\n".join(summary_parts) if summary_parts else None
    
    def reset_novel_seeds(self, novel_id: str):
        """重置某小说的种子（重新规划时调用）"""
        if novel_id in self._used_ids["novel_seeds"]:
            old_seeds = self._used_ids["novel_seeds"][novel_id]["seeds"]
            # 将旧种子归还到可用池
            self._used_ids["used_global"] = [i for i in self._used_ids["used_global"] if i not in old_seeds]
            del self._used_ids["novel_seeds"][novel_id]
        self._save_usage()


# 便捷函数
def create_seed_engine(storage_dir: str) -> CreativeSeedEngine:
    return CreativeSeedEngine(storage_dir)
