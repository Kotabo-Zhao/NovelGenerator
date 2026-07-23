"""NovelGenerator v2.2 — Requirement Decomposer Agent: 灵感深度拆解 + 阶段上下文注入

v2.2 增强:
- 旧: 关键词匹配 → 5个泛化子任务 → 无法指导具体生成
- 新: LLM深度语义分析 → 15-25个子任务 → 每个带 must_include/must_avoid/generation_hint
- 新增 decompose_to_context(): 将拆解结果转化为各阶段的精确上下文
- 新增 decompose_and_inject(): 一站式拆解+注入，返回增强后的 creative_input
"""

import json
import logging
from typing import Optional
from openai import OpenAI

log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════
# v2.2 增强版 System Prompt
# ═══════════════════════════════════════════════

DECOMPOSER_SYSTEM = """你是一位资深小说策划编辑和需求分析师。你的唯一任务是将用户的小说灵感拆解为可逐条执行的详细子任务。

## 核心原则

1. **深度挖掘**: 用户的每句话都藏着具体需求。不仅拆表面意思，更要拆隐含需求。
2. **穷尽细节**: 用户提到的每个关键词都要拆出对应的子任务，不能遗漏。
3. **具象化**: "主角很强" → "主角具备什么能力？强在哪里？怎么体现？有没有弱点？"
4. **可验证**: 每条子任务必须有明确的验证标准，可以判断"是/否"而非"好/坏"

## 拆解维度（必须逐个维度扫描，不能跳过）

### 1. 世界观 (worldbuilding)
- 时代背景: 古代/现代/架空？朝代风格？科技水平？
- 地理环境: 主要场景在哪？气候？特殊地貌？
- 力量体系: 修炼/魔法/科技？层级划分？核心规则？
- 社会结构: 阶级？宗门/帝国/联邦？势力关系？
- 核心矛盾: 世界面临什么危机？正邪如何定义？
- 特殊规则: 有什么与众不同的世界规则？

### 2. 角色 (character) — 必须拆解到每个人物
- 主角: 姓名？身份？性格？缺陷？动机？金手指？成长弧？秘密？
- 配角: 每个配角的身份、关系、作用、自身弧线
- 反派: 动机、实力、与主角的冲突本质、人性面
- 关系网: 人物之间的恩怨情仇、动态变化

### 3. 情节 (plot)
- 主线: 核心冲突是什么？起点→终点？
- 三幕分布: 建置→对抗→解决的节奏
- 关键转折: 不少于3个重大转折点
- 高潮设计: 最终对决的形式、情感、意义
- 子线: 爱情线？复仇线？成长线？

### 4. 风格 (style)
- 文笔特征: 简洁/华丽/幽默/冷峻？
- 对话风格: 自然口语/文绉绉/简洁有力？
- 节奏: 快节奏爽文/慢热铺垫/张弛有度？
- 情感基调: 热血/温情/虐心/轻松？
- 禁用套路: 要避免哪些AI常见的写法？

### 5. 结构 (structure)
- 卷章规划: 预计几卷？每卷功能？
- 视角: 第一人称/第三人称/多视角？
- 悬念布局: 开篇钩子、中期反转、结局收束
- 伏笔: 需要跨章节埋伏的关键信息

## 输出格式

```json
{
  "summary": "用户需求一句话概括",
  "core_theme": "故事核心主题（5-10字）",
  "target_audience": "目标读者群体",
  "subtasks": [
    {
      "id": "R001",
      "category": "worldbuilding|character|plot|style|structure",
      "sub_category": "更细的分类（如 character→protagonist, character→supporting, worldbuilding→power_system）",
      "title": "子任务简短标题（10字内）",
      "description": "具体要做什么，引述用户原话中的关键要求",
      "must_include": ["必须包含的元素1", "必须包含的元素2"],
      "must_avoid": ["必须避免的雷区1", "必须避免的雷区2"],
      "generation_hint": "给生成Agent的具体提示（注入到prompt中），说明这个子任务在生成时要注意什么",
      "quality_criteria": ["达标标准1（可判断是/否）", "达标标准2"],
      "priority": "P0|P1|P2",
      "depends_on": ["依赖的其他子任务ID"]
    }
  ],
  "total_count": 0,
  "integrity_check": {
    "covered_dimensions": ["worldbuilding", "character", "plot", "style", "structure"],
    "user_keywords_covered": ["用户提到的关键词是否都有对应子任务"],
    "missing_aspects": ["如果没有覆盖的方面，列出来"]
  }
}
```

## 拆解质量标准

- 总子任务数不少于 **12 条**（灵感越详细越多，上限 30 条）
- P0 子任务至少 **5 条**（核心需求）
- 每个维度至少有 **2 条**子任务
- 用户提到的每个人物、地点、事件都必须有独立子任务
- generation_hint 必须具体、可执行，不是泛泛的"注意XX"
- must_include 和 must_avoid 必须明确

只输出 JSON，不要其他内容。"""


class RequirementDecomposer:
    """需求拆解 Agent v2.2 — 灵感 → 深度结构化子任务 + 阶段上下文注入"""

    def __init__(self, client: OpenAI = None, model: str = None):
        self.client = client
        self.model = model

    def decompose(self, inspiration: str, plan: dict = None, 
                  existing_requirements: dict = None) -> dict:
        """将用户灵感拆解为子任务列表
        
        Args:
            inspiration: 用户输入的核心灵感
            plan: 已有的 plan 数据（如果有，用于增量拆解）
            existing_requirements: 已有的需求（用于追加/修改）
            
        Returns:
            {"summary": str, "subtasks": [...], "total_count": int, ...}
        """
        if not self.client or not self.model:
            return self._offline_decompose(inspiration, plan)

        context_parts = ["## 用户灵感\n" + inspiration]
        
        if plan:
            context_parts.append("\n## 已有创作方案（需在此基础上修改）")
            outline = plan.get("outline", {})
            chars = plan.get("characters", {})
            wb = plan.get("worldbuilding", {})
            context_parts.append(f"- 书名: {plan.get('title', '')}")
            context_parts.append(f"- 题材: {plan.get('genre', '')}  风格: {plan.get('style', '')}")
            context_parts.append(f"- 总章数: {outline.get('total_chapters', 0)}")
            context_parts.append(f"- 主角: {chars.get('protagonist', {}).get('name', '')}")
            context_parts.append(f"- 世界观: {wb.get('era', '')} / {wb.get('power_system', '')}")
        
        if existing_requirements:
            context_parts.append(f"\n## 已有需求（需追加/修改）")
            context_parts.append(json.dumps(existing_requirements, ensure_ascii=False)[:1000])

        user_prompt = "\n".join(context_parts)
        user_prompt += "\n\n请将以上灵感深度拆解为可执行子任务。要求不少于12条，每个维度至少2条。只输出 JSON。"

        log.info(f"RequirementDecomposer v2.2: analyzing '{inspiration[:80]}...'")

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": DECOMPOSER_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=8192,
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content
            result = json.loads(content)
            
            # 补充元数据
            result["original_inspiration"] = inspiration
            result["created_at"] = __import__("datetime").datetime.now().isoformat()
            result["status"] = "active"
            
            # 初始化所有子任务状态
            for t in result.get("subtasks", []):
                t["status"] = t.get("status", "pending")
                t["feedback"] = t.get("feedback", "")
                t["retry_count"] = t.get("retry_count", 0)
                # 确保字段存在
                t.setdefault("must_include", [])
                t.setdefault("must_avoid", [])
                t.setdefault("generation_hint", "")
                t.setdefault("sub_category", t.get("category", ""))
            
            count = len(result.get('subtasks', []))
            log.info(f"RequirementDecomposer v2.2: {count} subtasks generated")
            
            # 质量检查
            if count < 8:
                log.warning(f"RequirementDecomposer: only {count} subtasks (expected >=12), "
                           f"consider more detailed inspiration input")
            
            return result

        except json.JSONDecodeError as e:
            log.error(f"RequirementDecomposer JSON parse failed: {e}")
            return self._offline_decompose(inspiration, plan)
        except Exception as e:
            log.error(f"RequirementDecomposer failed: {e}")
            return self._offline_decompose(inspiration, plan)

    # ═══════════════════════════════════════════
    # v2.2 核心新增: 拆解结果 → 阶段上下文
    # ═══════════════════════════════════════════

    def decompose_to_context(self, requirements: dict) -> dict:
        """将拆解结果转化为各生成阶段的结构化上下文
        
        这是 v2.2 的核心方法。它将扁平化的子任务列表重组为:
        - 世界观生成需要的上下文
        - 角色生成需要的上下文
        - 大纲生成需要的上下文
        - 风格相关的上下文
        
        Returns:
            {
                "worldbuilding_context": "注入到世界观prompt的文本",
                "character_context": "注入到角色prompt的文本",
                "outline_context": "注入到大纲prompt的文本",
                "style_context": "注入到风格prompt的文本",
                "global_constraints": ["全局约束1", ...],
                "p0_requirements": ["P0级别的强制要求1", ...],
            }
        """
        subtasks = requirements.get("subtasks", [])
        if not subtasks:
            return self._default_context()

        context = {
            "worldbuilding_context": [],
            "character_context": [],
            "outline_context": [],
            "style_context": [],
            "global_constraints": [],
            "p0_requirements": [],
            "all_requirements": [],
        }

        for t in subtasks:
            cat = t.get("category", "")
            hint = t.get("generation_hint", "")
            title = t.get("title", "")
            desc = t.get("description", "")
            must_inc = t.get("must_include", [])
            must_avoid = t.get("must_avoid", [])
            priority = t.get("priority", "P1")

            # 构建单条需求的上下文文本
            parts = [f"【{title}】"]
            if desc and desc != title:
                parts.append(f"要求: {desc}")
            if must_inc:
                parts.append(f"必须包含: {', '.join(must_inc)}")
            if must_avoid:
                parts.append(f"必须避免: {', '.join(must_avoid)}")
            if hint:
                parts.append(f"提示: {hint}")
            
            requirement_text = "\n".join(parts)
            context["all_requirements"].append(requirement_text)

            # P0 级要求单独列出
            if priority == "P0":
                summary = f"[{title}] {desc[:100]}" if desc else title
                context["p0_requirements"].append(summary)
                if must_inc:
                    context["global_constraints"].extend(must_inc)

            # 按类别分发到对应阶段
            if cat == "worldbuilding":
                context["worldbuilding_context"].append(requirement_text)
            elif cat == "character":
                context["character_context"].append(requirement_text)
            elif cat == "plot":
                context["outline_context"].append(requirement_text)
            elif cat == "style":
                context["style_context"].append(requirement_text)
            elif cat == "structure":
                context["outline_context"].append(requirement_text)

        # 组装为文本块
        result = {}
        for key in ("worldbuilding_context", "character_context", "outline_context", "style_context"):
            items = context[key]
            if items:
                result[key] = "## 用户需求清单（请逐条满足）\n\n" + "\n\n---\n\n".join(items)
            else:
                result[key] = ""

        result["global_constraints"] = list(set(context["global_constraints"]))[:15]
        result["p0_requirements"] = context["p0_requirements"]
        result["all_requirements_text"] = "\n\n---\n\n".join(context["all_requirements"])
        result["summary"] = requirements.get("summary", "")
        result["core_theme"] = requirements.get("core_theme", "")

        return result

    def decompose_and_inject(self, inspiration: str, creative_input: dict = None) -> dict:
        """一站式: 拆解灵感 + 注入到 creative_input
        
        Args:
            inspiration: 用户灵感
            creative_input: 原始的创作参数 {genre, style, target_words, ...}
            
        Returns:
            增强后的 creative_input，包含 _requirements 和 _phase_context
        """
        result = dict(creative_input or {})
        result["inspiration"] = inspiration

        # Step 1: 深度拆解
        requirements = self.decompose(inspiration)
        result["_requirements"] = requirements

        # Step 2: 转化为阶段上下文
        phase_context = self.decompose_to_context(requirements)
        result["_phase_context"] = phase_context

        # Step 3: 构建增强版 inspiration（包含拆解后的需求摘要）
        enhanced_inspiration = self._build_enhanced_inspiration(inspiration, requirements)
        result["_enhanced_inspiration"] = enhanced_inspiration

        log.info(f"RequirementDecomposer v2.2: decompose_and_inject done, "
                f"{len(requirements.get('subtasks', []))} subtasks → context")
        return result

    def _build_enhanced_inspiration(self, original: str, requirements: dict) -> str:
        """从拆解结果构建增强版灵感描述，用于替代原始灵感注入 prompt"""
        subtasks = requirements.get("subtasks", [])
        if not subtasks:
            return original

        parts = [f"## 用户原始灵感\n{original}\n"]
        
        # 按类别组织
        by_cat = {}
        for t in subtasks:
            cat = t.get("category", "other")
            by_cat.setdefault(cat, []).append(t)

        cat_names = {
            "worldbuilding": "世界观要求",
            "character": "角色要求",
            "plot": "情节要求",
            "style": "风格要求",
            "structure": "结构要求",
        }

        for cat, cat_label in cat_names.items():
            tasks = by_cat.get(cat, [])
            if not tasks:
                continue
            parts.append(f"## {cat_label}")
            for t in tasks:
                title = t.get("title", "")
                desc = t.get("description", "")
                hint = t.get("generation_hint", "")
                must_inc = t.get("must_include", [])
                
                parts.append(f"\n### {title}")
                if desc and desc != title:
                    parts.append(f"要求: {desc}")
                if must_inc:
                    parts.append(f"必须包含: {'; '.join(must_inc)}")
                if hint:
                    parts.append(f"提示: {hint}")
        
        return "\n".join(parts)

    # ═══════════════════════════════════════════
    # 离线降级
    # ═══════════════════════════════════════════

    def _offline_decompose(self, inspiration: str, plan: dict = None) -> dict:
        """离线降级：基于关键词启发式拆解（v2.2 增强版，更多维度）"""
        subtasks = []
        rid = 1

        def add_task(cat, sub_cat, priority, title, desc, must_inc, must_avoid, hint, criteria):
            nonlocal rid
            subtasks.append({
                "id": f"R{rid:03d}", "category": cat, "sub_category": sub_cat,
                "title": title, "description": desc,
                "must_include": must_inc, "must_avoid": must_avoid,
                "generation_hint": hint, "quality_criteria": criteria,
                "priority": priority, "depends_on": [],
                "status": "pending", "feedback": "", "retry_count": 0,
            })
            rid += 1

        ins = inspiration

        # ── 世界观 ──
        add_task("worldbuilding", "era", "P0",
            "时代背景设定",
            f"确定故事发生的时代背景。灵感提及: {ins[:80]}",
            ["明确的时代/朝代/纪元名称", "技术水平描述", "社会风貌概述"],
            ["模糊的'架空世界'而不给具体细节", "与现代无差异的设定"],
            f"从灵感中提取时代线索: {ins[:60]}，构建有辨识度的世界背景",
            ["时代背景有具体名称和年代", "社会结构自洽", "与主线剧情有关联"])

        add_task("worldbuilding", "power_system", "P0",
            "力量/能力体系设计",
            f"定义本作的力量体系规则。灵感提及: {ins[:80]}",
            ["清晰的层级划分（至少3层）", "核心规则（力量来源/限制/代价）", "与主线冲突的关联"],
            ["模糊的'内力/灵力'而不给具体规则", "主角无代价无限升级"],
            "力量体系是世界观的核心，必须与主角成长弧和主线冲突绑定",
            ["力量层级清晰可辨", "有明确的限制和代价", "与情节推进有关联"])

        # 势力
        if any(kw in ins for kw in ["宗门", "帝国", "王朝", "组织", "势力", "家族", "帮派"]):
            add_task("worldbuilding", "factions", "P0",
                "势力/组织架构",
                f"设计故事中的核心势力。灵感提及: {ins[:80]}",
                ["至少3个势力/组织", "每个势力有明确的目标和立场", "势力间的冲突和联盟关系"],
                ["势力只是背景板不参与剧情", "势力设定脸谱化（纯好/纯坏）"],
                "势力是冲突的主要来源，每个势力应对主线产生实质性影响",
                ["势力数量≥3", "势力间关系清晰", "每个势力在主线中有作用"])

        # ── 角色 ──
        add_task("character", "protagonist", "P0",
            "主角完整档案",
            f"设计主角的完整人物档案。灵感提及: {ins[:80]}",
            ["姓名（自然中文名）", "身份/职业", "性格（表层+真实+缺陷）", "核心动机（外部目标+内部需求）", "成长弧线"],
            ["完美人设无缺陷", "名字用'叶尘''萧炎'等AI高频名", "动机不明确"],
            "主角是读者代入的窗口，必须有让人记住的特质和让人共鸣的缺陷",
            ["主角有清晰的身份和性格", "有明确的性格缺陷", "成长弧线有起点-中点-终点"])

        add_task("character", "supporting", "P1",
            "配角体系设计",
            f"设计2-4个核心配角。灵感提及: {ins[:80]}",
            ["每个配角有独立身份和性格", "与主角有明确的关系本质", "在主线中承担具体功能"],
            ["配角沦为工具人", "没有独立动机"],
            "配角不是主角的附属品，每个人都有自己的想要和害怕",
            ["配角数量2-4个", "每个有独立性格和功能", "与主角关系明确"])

        if any(kw in ins for kw in ["反派", "敌人", "对手", "仇人", "魔", "恶"]):
            add_task("character", "antagonist", "P0",
                "反派设计",
                f"设计1-2个核心反派。灵感提及: {ins[:80]}",
                ["有合理的动机（反派认为自己是'对的'）", "有与主角对等的实力或优势", "有人性面"],
                ["纯粹为恶而恶的反派", "动机单薄（就是想统治世界）"],
                "最好的反派是读者会犹豫'他说的好像也有道理'的角色",
                ["反派动机合理", "实力与主角对等或略强", "有让人共情的一面"])

        # ── 情节 ──
        add_task("plot", "main_plot", "P0",
            "主线情节规划",
            f"设计核心主线。灵感提及: {ins[:80]}",
            ["清晰的核心冲突", "三幕结构（建置→对抗→解决）", "至少3个关键转折点"],
            ["流水账式'然后...然后...'", "冲突不升级", "没有低谷"],
            "好的故事是'某人想要某物，但困难重重，最终通过改变自己获得（或失去）它'",
            ["主线冲突明确", "三幕结构完整", "关键转折点≥3个"])

        if any(kw in ins for kw in ["结局", "最后", "结尾", "收尾", "终", "决战"]):
            add_task("plot", "ending", "P1",
                "结局设计",
                f"规划故事的结局走向。灵感提及: {ins[:80]}",
                ["结局类型明确（圆满/悲剧/开放式）", "与主线冲突的解决方式", "主要角色的最终状态"],
                ["虎头蛇尾", "机械降神式解决", "结局与前面铺垫矛盾"],
                "结局是所有伏笔的回收点，必须在前面章节留够线索",
                ["结局类型明确", "与主线冲突呼应", "主要角色弧线完整"])

        # ── 风格 ──
        add_task("style", "narrative", "P1",
            "文笔与叙事风格",
            f"确定叙事风格。灵感提及: {ins[:80]}",
            ["明确的文笔特征", "对话风格", "节奏倾向"],
            ["过度修饰的AI套路描写", "角色对话千篇一律", "滥用'眼中闪过一丝XX'等模板"],
            "好文笔不是辞藻华丽，是用最少的字让读者看到画面、感受到情绪",
            ["文笔风格可描述", "对话风格一致", "有明确避免的套路"])

        add_task("style", "tone", "P1",
            "情感基调",
            f"确定故事的情感基调。灵感提及: {ins[:80]}",
            ["主要情感色彩（热血/温情/虐心/轻松/黑暗）", "情绪的起伏节奏"],
            ["全程同一情绪没有变化", "情绪转换突兀"],
            "情感基调是读者看完后的整体感受，需要与题材和风格匹配",
            ["情感基调可描述", "与题材匹配"])

        # ── 结构 ──
        add_task("structure", "chapter_plan", "P0",
            "章节结构规划",
            f"规划章节结构。灵感提及: {ins[:80]}",
            ["卷章数量合理", "每章有钩子", "高潮分布合理"],
            ["每章结尾平淡没有钩子", "连续多章没有实质性事件推进"],
            "每章的结尾决定了读者会不会点下一章，钩子是网文的第一生产力",
            ["卷章规划完整", "每章有钩子", "高潮点分布合理"])

        add_task("structure", "foreshadowing", "P1",
            "伏笔与悬念设计",
            "规划跨章节的伏笔和悬念",
            ["至少3个跨章节伏笔", "伏笔有合理的回收计划", "开篇悬念设置"],
            ["伏笔埋了不回收", "悬念设置过于明显", "伏笔与主线无关"],
            "伏笔是读者回看时惊叹'原来如此'的设计，需要提前规划回收点",
            ["伏笔≥3个", "每个伏笔有回收章节", "开篇有吸引力"])

        return {
            "original_inspiration": inspiration,
            "summary": f"用户需求: {inspiration[:100]}",
            "core_theme": inspiration[:20] if inspiration else "待定",
            "target_audience": "网文读者",
            "subtasks": subtasks,
            "total_count": len(subtasks),
            "created_at": __import__("datetime").datetime.now().isoformat(),
            "status": "active",
            "offline_mode": True,
            "integrity_check": {
                "covered_dimensions": ["worldbuilding", "character", "plot", "style", "structure"],
                "user_keywords_covered": ["离线模式，关键词覆盖"],
                "missing_aspects": [],
            }
        }

    def _default_context(self) -> dict:
        """空上下文的默认值"""
        return {
            "worldbuilding_context": "",
            "character_context": "",
            "outline_context": "",
            "style_context": "",
            "global_constraints": [],
            "p0_requirements": [],
            "all_requirements_text": "",
            "summary": "",
            "core_theme": "",
        }

    # ═══════════════════════════════════════════
    # 追加/修改需求
    # ═══════════════════════════════════════════

    def update_requirements(self, requirements: dict, 
                            new_inspiration: str) -> dict:
        """追加/修改需求后重新拆解，标记变更项"""
        old_subtasks = requirements.get("subtasks", [])
        old_ids = {t["id"] for t in old_subtasks}
        
        new_req = self.decompose(new_inspiration)
        new_subtasks = new_req.get("subtasks", [])
        
        for t in new_subtasks:
            if t["id"] not in old_ids:
                t["change_type"] = "new"
        
        merged = dict(requirements)
        merged["subtasks"] = old_subtasks + [t for t in new_subtasks if t["id"] not in old_ids]
        merged["total_count"] = len(merged["subtasks"])
        merged["updated_at"] = __import__("datetime").datetime.now().isoformat()
        merged["status"] = "active"
        
        return merged
