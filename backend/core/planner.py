"""NovelGenerator — Planner: 世界观、角色、大纲生成"""
import asyncio
import re
import json
import logging
from openai import OpenAI
from .styles import get_style, build_style_prompt, build_custom_style

log = logging.getLogger(__name__)

PLANNER_SYSTEM = """你是一位资深的小说策划编辑，专精于网文和类型小说的世界观搭建、角色设计和大纲规划。

你的任务是根据用户提供的创意，生成结构化的设定文档。你必须严格按照 JSON 格式输出。

## 目标风格

{style_guide}

## 三幕式结构规划（必须严格遵守）

无论小说有多少卷，整体叙事必须遵循经典三幕结构：

### 第一幕·建置（全书前25%）
- **功能**: 建立日常世界 → 引入核心冲突 → 主角做出不可逆的选择（第一道门）
- **要求**: 展示主角的初始状态和缺陷，埋下成长伏笔。世界规则在此幕全部交代清楚。
- **情绪**: 好奇→危机感→决意

### 第二幕·对抗（全书中间50%）
- **功能**: 主角在对抗中学习成长 → 遭遇中点转折（假胜利或惨败）→ 陷入最低谷（第二道门）
- **要求**: 每卷的对抗强度逐级加码。中点处有一次重大认知颠覆（发现真相/盟友背叛/力量代价）。
- **情绪**: 希望→挫折→绝望→重生之决心

### 第三幕·解决（全书最后25%）
- **功能**: 集结力量 → 决战 → 新平衡建立（或开放式结局）
- **要求**: 所有伏笔在此幕回收完毕。主角完成完整角色弧（从X到Y的转变）。
- **情绪**: 紧张→释放→余韵（或悲伤→接受→新生）

### 幕间门坎
- 第一道门（幕一→幕二）: 主角做出"无法回头"的选择，旧世界对他关闭
- 第二道门（幕二→幕三）: 最低谷后的顿悟，获得最终决战的钥匙（不是力量是认知）

## 输出要求

### 世界观 (worldbuilding)
- 时代背景、地理环境、社会结构
- 力量体系/科技水平（如有）
- 核心冲突（主线矛盾）
- 势力分布（3-5个势力/组织）

### 角色 (characters) — 人物宝典级深度
- 主角必须包含以下完整档案：
  * 基础: 姓名、年龄、身份、外貌
  * 性格光谱: 表层性格（对外展现）+ 真实性格（独处/危机时暴露）+ 性格缺陷（必须存在的弱点）
  * 成长弧: 初始状态 → 中点转变 → 最终状态（从什么变成什么）
  * 核心动机: 外部目标（想要什么）+ 内部需求（真正需要什么）
  * 秘密: 至少一个不为他人所知的秘密
  * 口头禅/习惯动作: 标志性的说话方式或行为习惯
  * 金手指/特殊能力（如有）
  * 关系网: 与3个最重要角色的关系本质
- 配角: 4-6个，每人包含姓名、身份、与主角关系、性格特征、在主线中的作用、自身小弧线、对主角的意义（盟友/导师/镜子/对手/爱的对象）
- 反派: 1-2个，包含动机、实力、与主角的冲突点、为什么反派认为自己是"对的"

### 大纲 (outline)
- 卷结构：3-5卷，每卷标注属于三幕中的哪一幕
- 章纲：每卷5-10章，每章包含：核心事件、情绪曲线、出场角色、本章钩子
- 总字数目标

## 写作原则

{structure_hint}

- 每章结尾必须有钩子（悬念/期待）
- 角色弧线：主角必须有成长变化
- 避免流水账，每章必须有实质性事件推进
- 每章必须遵循三态情绪弧线：压抑态（前1/3铺垫障碍）→ 爆发态（中段冲突引爆）→ 余韵态（结尾留白/悬念）
- 每章指定场景主导类型：动作场景（战斗/追逐）、情感场景（内心挣扎/关系转折）、对话场景（信息博弈/意志碰撞）
- 每章指定冲突类型与强度：[IN内心/IR人际/EN环境/DE宿命]:强度1-5
- 每章必须包含场景节拍（scene beats）：将章节拆为3-5个关键节拍，每个节拍标注功能（开篇钩子/冲突升级/中点转折/高潮/收尾钩子）
- 生成的大纲必须完全符合目标风格的{style_name}的结构特征
"""


class Planner:
    """故事规划器 — 生成世界观、角色、大纲"""

    def __init__(self, client: OpenAI, model: str):
        self.client = client
        self.model = model

    def plan(self, creative_input: dict) -> dict:
        """
        Args:
            creative_input: {
                "genre": "修仙",
                "style": "土豆风格",
                "inspiration": "程序员穿越修真界，用代码重构修仙体系",
                "target_words": 1000000
            }
        Returns:
            structured plan dict
        """
        genre = creative_input.get("genre", "玄幻")
        style_name = creative_input.get("style", "热血爽文")
        inspiration = creative_input.get("inspiration", "")
        target_words = creative_input.get("target_words", 500000)
        title = creative_input.get("title", "")
        normal_pacing = creative_input.get("normal_pacing", False)

        # 获取风格模板（支持自定义风格）
        if style_name.startswith("自定义") or style_name == "自定义风格":
            style_config = build_custom_style(style_name)
        else:
            style_config = get_style(style_name)
        
        style_guide = build_style_prompt(style_config)
        structure_hint = style_config.get("structure",
            "遵循「三章一小高潮、五章一中高潮、一卷一大高潮」的节奏")

        # v2.2: 节奏指令
        pacing_block = ""
        if normal_pacing:
            pacing_block = "\n【节奏】正常节奏 — 铺陈充分，张弛有度，允许慢热铺垫和细节展开\n"
        else:
            pacing_block = "\n【节奏】快节奏 — 短平快！开篇即冲突，章章有事件推进，章末有强钩子，拒绝纯铺垫。每3章一小高潮，每5章一中高潮。对话简洁，描写精炼，世界观通过行动展现。\n"

        system_prompt = PLANNER_SYSTEM.format(
            style_guide=style_guide,
            style_name=style_config['name'],
            structure_hint=structure_hint,
        )

        user_prompt = f"""请根据以下信息生成完整的创作方案。

【题材】{genre}
【风格】{style_name}（{style_config['author']}）
【核心创意】{inspiration}
【目标字数】{target_words} 字
{'' if title else '【书名】请根据创意自动生成一个有吸引力的书名'}{pacing_block}
{f'【书名】{title}' if title else ''}

请严格按照以下 JSON 格式输出（不要输出其他内容）：

```json
{{
  "title": "书名",
  "genre": "题材",
  "style": "{style_name}",
  "target_words": {target_words},
  "worldbuilding": {{
    "era": "时代背景",
    "geography": "地理环境（3-5个关键地点）",
    "power_system": "力量/科技体系",
    "core_conflict": "核心矛盾",
    "factions": [
      {{"name": "势力名", "description": "描述", "alignment": "正/邪/中立"}}
    ]
  }},
  "characters": {{
    "protagonist": {{
      "name": "姓名",
      "age": "年龄",
      "identity": "身份",
      "appearance": "外貌描述",
      "personality": {{
        "surface": "表层性格（对外展现的样子）",
        "true_self": "真实性格（独处或危机时的样子）",
        "flaw": "性格缺陷（必须存在的弱点，不要完美人设）"
      }},
      "backstory": "背景故事（不要超过100字，但必须包含形成性格的关键事件）",
      "motivation": {{
        "want": "外部目标（想要什么——权力/复仇/守护/自由）",
        "need": "内部需求（真正需要什么——被认可/放下/学会信任/找到归属）"
      }},
      "arc": "角色成长弧（初始状态 → 中点转变 → 最终状态）",
      "cheat": "金手指/特殊能力（如有）",
      "secret": "不为他人所知的秘密（至少一个）",
      "catchphrase": "口头禅或标志性习惯动作",
      "relationships": [
        {{"name": "", "type": "盟友/导师/镜子/对手/爱的对象", "dynamic": "关系本质（如：表面敌对实为互相欣赏）"}}
      ]
    }},
    "supporting": [
      {{
        "name": "",
        "identity": "",
        "relation": "",
        "personality": "",
        "role": "在主线中的作用",
        "mini_arc": "自身小弧线",
        "meaning": "对主角的意义（盟友/导师/镜子/对手/爱的对象）"
      }}
    ],
    "antagonist": [
      {{
        "name": "",
        "motivation": "动机（为什么反派认为自己是'对的'？）",
        "power": "实力",
        "conflict": "与主角的根本冲突点",
        "humanity": "反派的人性面（不要纯粹的恶）"
      }}
    ],
    "bible_summary": "人物关系总览：谁是谁的什么，有什么恩怨，会如何变化"
  }},
  "outline": {{
    "volumes": [
      {{
        "number": 1,
        "title": "卷标题",
        "act": "第一幕·建置/第二幕·对抗/第三幕·解决",
        "theme": "本卷主题",
        "act_function": "本卷在三幕中的功能（如：建立世界观+主角踏上征程 / 对抗逐渐升级+中点转折 / 最终决战+新平衡建立）",
        "chapters": [
          {{
            "number": 1,
            "title": "章标题",
            "summary": "核心事件一句话描述",
            "scene_type": "动作场景/情感场景/对话场景/混合",
            "emotion_curve": "压抑→爆发→余韵（本章情绪弧线）",
            "conflict": "冲突描述与类型[IN内心/IR人际/EN环境/DE宿命]:强度1-5",
            "scene_beats": [
              {{"beat": 1, "name": "开篇钩子", "function": "用动作/对话/悬念立即抓住读者", "key_action": "具体发生了什么"}},
              {{"beat": 2, "name": "冲突升级", "function": "障碍增加/信息揭露/矛盾激化", "key_action": ""}},
              {{"beat": 3, "name": "转折/高潮", "function": "本章最重要的转折点或情绪爆发", "key_action": ""}},
              {{"beat": 4, "name": "余波/收束", "function": "高潮后的缓冲和反应", "key_action": ""}},
              {{"beat": 5, "name": "钩子", "function": "留下悬念或期待，引向下一章", "key_action": ""}}
            ],
            "characters": ["出场角色"],
            "hook": "本章结尾钩子（余韵态具体内容）",
            "target_words": 1500
          }}
        ]
      }}
    ],
    "total_chapters": 0,
    "three_act_map": "用一句话描述三幕在全书的分布：第一幕（建置）第X章-第Y章、第二幕（对抗）第X章-第Y章（中点在第Z章）、第三幕（解决）第X章-第Y章",
    "rhythm_notes": "节奏设计说明（包括高潮分布、张弛比例、关键反转位置）"
  }}
}}
```
重要：
- 确保 JSON 是有效的（注意逗号、引号、括号匹配），不要包含注释。
- 大纲的卷结构和节奏必须严格匹配「{style_config['name']}」的风格要求。

【章节标题多样性要求】
- 禁止所有章节使用同一格式模板（如「XXXX·XX」「XX者」「XX的XX」）
- 标题应多样化：可以来自动作、对话、意象、悬念、细节
- 相邻章节标题风格应明显不同，长短交错
- 示例好标题: 「墙上的影子先碎了」「三碗酒」「剑还在转」「他不叫叶凡」"""
        log.info(f"Planning novel: {genre}/{style_name} - {inspiration[:50]}...")
        
        kwargs = dict(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.8,
            max_tokens=8192,
        )
        if "v4" in self.model:
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        
        response = self.client.chat.completions.create(**kwargs)

        content = response.choices[0].message.content
        plan = self._parse_json(content)
        
        if plan:
            # 标准化章节号
            for vol in plan.get("outline", {}).get("volumes", []):
                if not isinstance(vol, dict):
                    continue
                vol["number"] = int(vol.get("number", 1))
                for ch in vol.get("chapters", []):
                    if isinstance(ch, dict):
                        ch["number"] = int(ch.get("number", 1))
                        ch["target_words"] = int(ch.get("target_words", 1500))
            if isinstance(plan.get("outline"), dict):
                plan["outline"]["total_chapters"] = int(plan.get("outline", {}).get("total_chapters", 0))
            plan["target_words"] = int(plan.get("target_words", 0))
            plan["style"] = style_name
            
            # v2: 大纲验证与自动修复
            plan["outline"] = self.repair_outline(plan["outline"])
            
            plan["_meta"] = {
                "created_at": __import__("datetime").datetime.now().isoformat(),
                "model": self.model,
                "creative_input": creative_input,
            }
            log.info(f"Plan generated: {plan.get('title', 'Unknown')} — "
                     f"{plan.get('outline', {}).get('total_chapters', 0)} chapters")
        
        return plan

    async def plan_stream(self, creative_input: dict):
        """3阶段流式规划 — 失败自动降级，保证始终返回可用计划
        
        v2.2: 支持 requirements_context（来自 RequirementDecomposer 的拆解结果）
        将其注入到各阶段 prompt 中，确保生成内容满足用户需求。
        """
        fallback_count = 0
        genre = creative_input.get("genre", "玄幻")
        style_name = creative_input.get("style", "热血爽文")
        inspiration = creative_input.get("inspiration", "")
        # v2.2: 支持增强版灵感（来自需求拆解）
        enhanced_inspiration = creative_input.get("_enhanced_inspiration", "")
        target_words = creative_input.get("target_words", 500000)
        title = creative_input.get("title", "")
        
        # v2.2: 节奏模式 — 默认快节奏(False)
        normal_pacing = creative_input.get("normal_pacing", False)
        
        # 构建节奏指令
        if normal_pacing:
            pacing_instruction = """## 节奏：正常节奏（铺陈充分）

- 每章有完整的起承转合，允许慢热铺垫
- 角色心理和世界观细节可以充分展开
- 冲突逐级递进，给读者喘息空间
- 章节间可以有过渡性的日常/对话场景
- 伏笔可以跨较长章节慢慢展开"""
        else:
            pacing_instruction = """## 节奏：快节奏（默认 · 短平快）

- 每章必须有明确的事件推进，拒绝纯铺垫
- 开篇即冲突——前三段就要抓住读者
- 章末钩子必须有强悬念（生死/秘密/背叛/实力暴涨）
- 跳过冗长的角色心理描写和环境描写
- 冲突密度高：每3章一个小高潮，每5章一个中高潮
- 对话简洁有力，不写无意义的寒暄
- 世界观通过行动和冲突自然展现，不做教科式介绍
- 每章字数集中在一个核心事件上，不枝蔓"""
        
        # v2.2: 阶段上下文注入
        phase_context = creative_input.get("_phase_context", {}) or {}
        wb_context = phase_context.get("worldbuilding_context", "")
        char_context = phase_context.get("character_context", "")
        outline_context = phase_context.get("outline_context", "")
        style_context = phase_context.get("style_context", "")
        global_constraints = phase_context.get("global_constraints", [])
        p0_reqs = phase_context.get("p0_requirements", [])
        
        # 如果有增强版灵感，用它替代原始灵感
        effective_inspiration = enhanced_inspiration or inspiration

        if style_name.startswith("自定义") or style_name == "自定义风格":
            style_config = build_custom_style(style_name)
        else:
            style_config = get_style(style_name)
        
        style_guide = build_style_prompt(style_config)
        structure_hint = style_config.get("structure",
            "遵循「三章一小高潮、五章一中高潮、一卷一大高潮」的节奏")

        # v2.2: 全局约束提示
        constraints_note = ""
        if global_constraints:
            constraints_note = "\n\n## ⚠️ 全局约束（必须全部满足）\n" + "\n".join(
                f"- {c}" for c in global_constraints[:10]
            )
        
        yield {"type": "progress", "phase": "start", "pct": 5, "label": "分析创意…"}

        # ── Phase 1: 世界观 (5% → 30%) ──
        yield {"type": "progress", "phase": "worldbuilding", "pct": 10, "label": "构建世界观体系…"}
        
        # v2.2: 注入世界观相关需求
        wb_requirements_block = ""
        if wb_context:
            wb_requirements_block = f"""

## ⚠️ 用户需求（必须逐条满足，不得遗漏）

{wb_context}
"""
        
        wb_prompt = f"""你是一位世界观架构师。请根据以下创意和用户需求生成世界设定。

风格: {style_config['name']} ({style_config['author']})
创意: {effective_inspiration}
题材: {genre}
{wb_requirements_block}
{constraints_note}

输出 JSON:
```json
{{
  "title": "自动生成的书名",
  "genre": "{genre}",
  "worldbuilding": {{
    "era": "时代背景",
    "geography": "地理环境（3-5个关键地点）",
    "power_system": "力量/科技体系",
    "core_conflict": "核心矛盾（主线冲突的本质）",
    "factions": [{{"name":"","description":"","alignment":"正/邪/中立"}}]
  }}
}}
```
只输出 JSON。"""

        wb = await self._call_llm(wb_prompt, "worldbuilding")
        if not wb:
            # 降级: 使用默认世界观
            log.warning("Worldbuilding failed, using default")
            wb = {
                "title": title or inspiration[:15] or "未命名小说",
                "genre": genre,
                "worldbuilding": {
                    "era": "架空世界",
                    "geography": "待扩展",
                    "power_system": "待定义",
                    "core_conflict": inspiration[:50] if inspiration else "待定义",
                    "factions": [{"name":"主角阵营","description":"","alignment":"正"}]
                }
            }
            yield {"type": "warning", "message": "世界观生成部分降级，已使用默认设定"}
            fallback_count += 1

        yield {"type": "progress", "phase": "worldbuilding", "pct": 30, "label": "世界观完成 ✓"}

        # v2.2: 构建可读的世界观摘要（用于后续阶段注入）
        _wb = wb.get('worldbuilding', {})
        _factions = _wb.get('factions', [])
        _faction_text = ""
        if isinstance(_factions, list):
            for f in _factions[:6]:
                if isinstance(f, dict):
                    _faction_text += f"- {f.get('name','')} ({f.get('alignment','中立')}): {f.get('description','')[:60]}\n"
        wb_summary_compact = f"""## 🌍 世界观
- 时代: {_wb.get('era', '')}
- 地点: {_wb.get('geography', '')[:200]}
- 力量体系: {_wb.get('power_system', '')[:200]}
- 核心冲突: {_wb.get('core_conflict', '')}
- 势力: {_faction_text.strip() or '待展开'}"""

        # ── Phase 2: 角色 (30% → 55%) ──
        yield {"type": "progress", "phase": "characters", "pct": 32, "label": "设计角色关系网…"}
        
        natural_names = creative_input.get("natural_names", True)
        
        naming_rules = """## 命名规则（反AI套路）
- 禁止使用以下网文高频字根：云、星辰、无极、天、剑、血、魔、帝、皇、王、尊、圣、仙、神、龙、凤、麒、麟、冥、魂、煞
- 主角名不能是"叶尘""萧炎""林动""林枫""苏铭""韩立"等AI高频名或其变体
- 用真实感的中文名：考虑时代背景（古代/现代/架空），考虑阶级身份（平民/世家/皇室）
- 两字姓可用（欧阳、慕容、上官），单字姓更自然。名用1-2字，避免单字玄幻名
- 配角名要区分度：不同阵营、不同阶层用不同风格的名字
- 反派名要有"人味"——不是天生邪恶，是境遇造就。禁用"暗""影""煞""灭"等标签化命名
- 示例好名字: 周怀瑾, 柳如意, 沈砚, 顾长卿, 卫小蝶, 曹阿满, 姜白石, 陆青崖
"""

        # v2.2: 注入角色相关需求
        char_requirements_block = ""
        if char_context:
            char_requirements_block = f"""

## ⚠️ 用户角色需求（必须逐条满足，不得遗漏）

{char_context}
"""
        if style_context:
            char_requirements_block += f"""

## 风格要求

{style_context}
"""

        char_prompt = f"""你是一位角色设计师。请根据以下世界观和用户需求为小说创作角色体系。

{wb_summary_compact}
风格: {style_config['name']}
创意: {effective_inspiration}
{char_requirements_block}
{constraints_note}

{naming_rules}

输出 JSON（主角+配角+反派，深度人物宝典级别）:
```json
{{
  "characters": {{
    "protagonist": {{
      "name": "", "age": "", "identity": "", "appearance": "",
      "personality": {{"surface": "","true_self": "","flaw": ""}},
      "backstory": "", "motivation": {{"want": "","need": ""}},
      "arc": "", "cheat": "", "secret": "", "catchphrase": "",
      "relationships": [{{"name":"","type":"盟友/导师/对手","dynamic":""}}]
    }},
    "supporting": [{{"name":"","identity":"","relation":"","personality":"","role":"","mini_arc":"","meaning":""}}],
    "antagonist": [{{"name":"","motivation":"","power":"","conflict":"","humanity":""}}],
    "bible_summary": ""
  }}
}}
```
只输出 JSON。"""

        chars = await self._call_llm(char_prompt, "characters")
        if not chars:
            # 降级: 使用默认角色
            log.warning("Characters generation failed, using default")
            chars = {
                "characters": {
                    "protagonist": {
                        "name": inspiration[:3] if inspiration else "主角",
                        "identity": "修士", "age": "18",
                        "personality": {"surface":"坚韧","true_self":"善良","flaw":"执着"},
                        "arc": "成长变强",
                        "motivation": {"want":"变强","need":"被认可"},
                        "cheat": "待揭示", "secret": "待揭示",
                    },
                    "supporting": [{"name":"挚友","identity":"同伴","relation":"挚友","personality":"忠诚","role":"助手","meaning":"盟友"}],
                    "antagonist": [{"name":"宿敌","motivation":"理念冲突","power":"强大","conflict":"生存竞争","humanity":"有苦衷"}],
                    "bible_summary": "主角与挚友并肩对抗宿敌"
                }
            }
            yield {"type": "warning", "message": "角色生成部分降级，已使用默认设定"}
            fallback_count += 1

        yield {"type": "progress", "phase": "characters", "pct": 55, "label": "角色设计完成 ✓"}

        # ── Phase 3: 大纲 (55% → 95%) ──
        # ── Phase 3: 大纲（分卷生成，每卷独立 LLM 调用，杜绝截断）──
        # v2.2: 按字数+节奏动态计算章节数
        # 快节奏：少章多字，每章信息密度高，冲突集中
        # 正常节奏：多章均衡，铺陈充分，信息密度适中
        tw = target_words
        if normal_pacing:
            # 正常节奏：更短章节，更丰富的细节展开
            if tw <= 10000:    chapter_words, max_ch = 1000, 12
            elif tw <= 50000:  chapter_words, max_ch = 1500, 35
            elif tw <= 150000: chapter_words, max_ch = 2000, 55
            else:              chapter_words, max_ch = 2000, 70   # 20W-30W
        else:
            # 默认快节奏：更长章节，少章节高密度，拒绝灌水
            if tw <= 10000:    chapter_words, max_ch = 1500, 10
            elif tw <= 50000:  chapter_words, max_ch = 2500, 20
            elif tw <= 150000: chapter_words, max_ch = 3000, 40
            else:              chapter_words, max_ch = 3000, 60   # 20W-30W
        
        estimated_chapters = min(max_ch, max(8, tw // chapter_words))
        vol_count = max(2, min(5, estimated_chapters // 8))   # 卷数控制在2-5卷
        ch_per_vol = max(5, min(12, estimated_chapters // vol_count))

        # v2.2: 构建角色花名册 — 每卷生成时注入，防止重新发明角色
        protagonist = chars.get('characters', {}).get('protagonist', {})
        supporting = chars.get('characters', {}).get('supporting', [])
        antagonists = chars.get('characters', {}).get('antagonist', [])
        
        character_roster = f"""## 👥 角色花名册（全书角色池，不要创造重复功能的新角色）

### 主角
- {protagonist.get('name', '主角')} — {protagonist.get('identity', '')} · {protagonist.get('personality', {}).get('surface', '') if isinstance(protagonist.get('personality'), dict) else protagonist.get('personality', '')}
  动机: {protagonist.get('motivation', {}).get('want', '') if isinstance(protagonist.get('motivation'), dict) else ''}
  成长弧: {protagonist.get('arc', '')}
  {'金手指: ' + protagonist.get('cheat', '') if protagonist.get('cheat') else ''}

### 已设定配角（必须使用这些角色，不要新造同功能的替代品）
"""
        for i, c in enumerate(supporting[:6]):
            character_roster += f"- {c.get('name', f'配角{i+1}')} — {c.get('identity', '')} · {c.get('relation', '')} · 作用: {c.get('role', '')} · 弧线: {c.get('mini_arc', '')}\n"
        
        if antagonists:
            character_roster += "\n### 已设定反派\n"
            for i, c in enumerate(antagonists[:3]):
                character_roster += f"- {c.get('name', f'反派{i+1}')} — {c.get('conflict', '')} · 动机: {c.get('motivation', '')}\n"
        
        if support := chars.get('characters', {}).get('bible_summary', ''):
            character_roster += f"\n### 人物关系\n{support[:300]}\n"
        
        character_roster += "\n**⚠️ 以上就是本书所有角色。禁止创造功能重复的新角色。需要新角色时必须给出与已有角色不同的独立身份和功能。**"

        # v2.2: 复用已构建的世界观摘要
        worldbuilding_summary = wb_summary_compact

        # Phase 3a: 生成卷结构
        yield {"type": "progress", "phase": "outline", "pct": 58, "label": "规划卷结构…"}

        # v2.2: 注入大纲相关需求
        outline_requirements_block = ""
        if outline_context:
            outline_requirements_block = f"""

## ⚠️ 用户情节/结构需求（必须逐条满足，不得遗漏）

{outline_context}
"""

        structure_prompt = f"""你是小说结构师。根据设定和用户需求规划{vol_count}卷结构。

{worldbuilding_summary}
{character_roster}
创意: {inspiration}  风格: {style_config['name']}  总目标: {target_words}字
{pacing_instruction}
{outline_requirements_block}
{constraints_note}

只输出JSON数组，{vol_count}个对象:
```json
[
  {{"vol":1,"title":"卷标题","act":"第一幕·建置","theme":"核心主题","ch_count":{ch_per_vol},"act_function":"本卷在全剧中的作用"}}
]
```
只输出JSON。"""

        structure_result = await self._call_llm(structure_prompt, "outline_structure", max_tokens=2048)
        
        # Fallback volume structure — 支持 {"volumes": [...]} 或 {"data": [...]} 或 直接 [...]
        volumes_meta = []
        if structure_result:
            if isinstance(structure_result, list):
                volumes_meta = structure_result
            elif isinstance(structure_result, dict):
                volumes_meta = structure_result.get("data") or structure_result.get("volumes", [])
            if not volumes_meta and isinstance(structure_result, dict):
                for v in structure_result.values():
                    if isinstance(v, list): volumes_meta = v; break
        
        if not volumes_meta:
            log.warning("Volume structure failed, using fallback")
            volumes_meta = [{"vol": i+1, "title": f"第{i+1}卷", "act": ["第一幕·建置","第二幕·对抗","第三幕·解决"][min(i,2)],
                            "theme": "主线推进", "ch_count": ch_per_vol, "act_function": "推进故事"}
                           for i in range(vol_count)]
            yield {"type": "warning", "message": "卷结构使用默认规划"}
            fallback_count += 1

        # Phase 3b+: 逐卷生成章节
        all_volumes = []
        chapter_counter = 0
        total_chapters = sum(v.get("ch_count", ch_per_vol) for v in volumes_meta[:vol_count])
        
        # v2.2: 跟踪前卷摘要+已用情节元素，防止跨卷重复
        prev_volumes_summary = []
        used_plot_elements = []  # 追踪已在前卷使用过的关键情节元素
        last_volume_hook = None   # 上一卷结尾钩子
        introduced_characters = []  # 追踪已出场的角色名

        for idx, vol_meta in enumerate(volumes_meta[:vol_count]):
            vol_num = vol_meta.get("vol", idx + 1)
            vol_title = vol_meta.get("title", f"第{vol_num}卷")
            vol_act = vol_meta.get("act", "")
            vol_theme = vol_meta.get("theme", "")
            vol_function = vol_meta.get("act_function", "")
            n_ch = min(10, vol_meta.get("ch_count", ch_per_vol))  # 单卷上限10章

            pct = 58 + int(34 * (idx + 1) / vol_count)
            yield {"type": "progress", "phase": "outline", "pct": pct,
                   "label": f"生成第{vol_num}卷「{vol_title}」({n_ch}章)…"}

            # v2.2: 构建前卷上下文（防止故事来回循环）
            prev_context = ""
            if prev_volumes_summary:
                prev_context = "## ⚠️ 前卷已写内容（严禁重复！故事必须向前推进）\n\n"
                for pv in prev_volumes_summary:
                    prev_context += f"- 第{pv['vol']}卷「{pv['title']}」({pv['act']}):\n"
                    for ps in pv['summaries']:
                        prev_context += f"  第{ps['ch']}章: {ps['summary']}\n"
                # v2.2: 上一卷结尾钩子
                if last_volume_hook:
                    prev_context += f"\n**🔗 上一卷结尾钩子（本章必须回应）: {last_volume_hook}**\n"
                prev_context += "\n**重要：以上内容已经写过了，本卷必须推进新剧情，不要重复前面的模式或事件！**\n"
                if used_plot_elements:
                    prev_context += f"\n**前卷已使用的情节元素（本卷禁止再用！）: {', '.join(used_plot_elements)}**\n"
                # v2.2: 已出场角色状态
                if introduced_characters:
                    prev_context += f"\n**前卷已出场角色: {', '.join(introduced_characters)}\n这些角色已经存在于故事中，后续卷可以直接使用，不要重新介绍他们。**\n"

            # v2.2: 需求块标注 — 需求应分布在不同卷，不要每卷重复
            distributed_req_block = ""
            if outline_requirements_block:
                distributed_req_block = f"""## ⚠️ 用户需求清单（分布在全书中，本卷只需满足其中一部分）

{outline_requirements_block}

**关键: 以上需求需要分布在{vol_count}卷中逐步满足，本卷是第{vol_num}卷（共{vol_count}卷），你只需要从需求清单中选取与第{vol_num}卷主题最相关、最合理出现在此卷的需求来满足。不要把全部需求塞进这一卷。**
"""

            ch_prompt = f"""生成第{vol_num}卷「{vol_title}」的{n_ch}章大纲。
卷信息: act={vol_act}, theme={vol_theme}, function={vol_function}
起始章号: {chapter_counter + 1}
本卷进度: 第{vol_num}/{vol_count}卷

{worldbuilding_summary}
{character_roster}
风格: {style_config['name']}
{pacing_instruction}
{prev_context}
{distributed_req_block}

【防止情节重复 — 绝对最重要】
- 这是第{vol_num}卷，前面已经有{vol_num-1}卷写过了。本卷的故事必须是全新的、在前几卷基础上递进的推进
- 前卷已经使用的情节元素（人物关系、冲突模式、场景类型）本卷禁止重复
- 如果前卷有"男主叫女主皇嫂"的情节，本卷绝对不能以此为核心事件，除非是矛盾升级后的质变（如关系破裂/升级而不是同样的互动模式）
- 每章的核心事件必须与前面所有章节不同
- 冲突层次必须升级：个人恩怨→组织对抗→世界观层面的冲突
- 角色的能力和认知必须在本卷有实质性的进步
- **不要重复同一个互动模式**：如果前面出现过"男主因为身份关系被看不起"，本卷不能再用同样的冲突方式

【章节标题多样性要求（关键！）】
- 禁止使用固定格式模板，每章标题应该有独特风格
- 禁止的格式: 「XXXX·XX」「事件名*角色名」「名词的XX」「XX者」「XX之路」「XX之X」
- 好标题举例: 「墙上的影子先碎了」「他不叫叶凡」「三碗酒」「剑还在转」「那天雨很大」
- 坏标题举例: 「觉醒·初战」「修炼者之路」「力量的真谛」「守护者」「复仇之火」
- 标题来源可以来自: 一个动作、一句对话、一个意象、一个悬念、一个细节
- 相邻章节的标题风格应有明显差异，长短交替

只输出JSON数组，{n_ch}个章节对象:
```json
[
  {{"number":{chapter_counter+1},"title":"章节标题(禁止格式模板)","summary":"30字内核心事件","emotion_curve":"压抑→爆发→余韵","conflict":"冲突描述","characters":["出场角色"],"hook":"结尾钩子","target_words":{chapter_words}}}
]
```
只输出JSON。"""

            ch_result = await self._call_llm(ch_prompt, f"outline_vol{vol_num}", max_tokens=4096)
            if not ch_result:
                # 单卷失败 → 生成最小化fallback章节
                log.warning(f"Volume {vol_num} chapter generation failed, using fallback")
                fallback_count += 1
                ch_result = [
                    {"number": chapter_counter + j + 1, "title": f"第{chapter_counter + j + 1}章",
                     "summary": f"第{vol_num}卷第{j+1}章核心剧情", "emotion_curve": "平稳→起伏→悬念",
                     "conflict": "主线推进", "characters": ["主角"],
                     "hook": "引导下一章", "target_words": chapter_words}
                    for j in range(n_ch)
                ]

            # Handle both dict wrapper ({"data": [...]}, {"chapters": [...]}) and direct array
            chapters = None
            if isinstance(ch_result, list):
                chapters = ch_result
            elif isinstance(ch_result, dict):
                chapters = ch_result.get("data") or ch_result.get("chapters")
                if not chapters:
                    for v in ch_result.values():
                        if isinstance(v, list): chapters = v; break
            if not chapters:
                chapters = []

            # Renumber to ensure continuity — 防御：确保每个 ch 都是 dict
            for j, ch in enumerate(chapters):
                if not isinstance(ch, dict):
                    log.error(f"Chapter {j+1} in vol {vol_num} is {type(ch).__name__}, not dict. Replacing with fallback.")
                    ch = {"number": chapter_counter + j + 1, "title": f"第{chapter_counter + j + 1}章",
                          "summary": f"第{vol_num}卷第{j+1}章", "emotion_curve": "平稳→起伏→悬念",
                          "conflict": "主线推进", "characters": ["主角"], "hook": "引导下一章",
                          "target_words": chapter_words}
                    chapters[j] = ch
                ch["number"] = chapter_counter + j + 1
                if "target_words" not in ch:
                    ch["target_words"] = chapter_words

            all_volumes.append({
                "number": vol_num,
                "title": vol_title,
                "act": vol_act,
                "theme": vol_theme,
                "act_function": vol_function,
                "chapters": chapters
            })
            chapter_counter += len(chapters)
            
            # v2.2: 记录本卷摘要+提取关键情节元素（防止后续卷重复）
            vol_summaries = []
            vol_elements = set()
            vol_hook = None
            vol_characters = set()
            for i, ch in enumerate(chapters[:6]):
                if isinstance(ch, dict):
                    summary = ch.get("summary", "")
                    conflict = ch.get("conflict", "")
                    hook = ch.get("hook", "")
                    combined = f"{summary} {conflict} {hook}"
                    vol_summaries.append({
                        "ch": ch.get("number", "?"), 
                        "summary": summary[:40]
                    })
                    # 提取关键情节元素
                    for kw in ["皇嫂", "嫂子", "师叔", "师父", "仇人", "父子", "师徒",
                                "退婚", "背叛", "夺宝", "比试", "宗门", "秘境",
                                "身份暴露", "实力暴涨", "复仇", "联姻", "刺杀"]:
                        if kw in combined:
                            vol_elements.add(kw)
                    # 追踪出场角色
                    for name in (ch.get("characters", []) or []):
                        if isinstance(name, str) and name not in ("主角", "配角", "反派"):
                            vol_characters.add(name)
                    # 追踪最后一章的钩子
                    if i == len(chapters[:6]) - 1 or ch.get("hook"):
                        vol_hook = hook if hook else vol_hook
            prev_volumes_summary.append({
                "vol": vol_num,
                "title": vol_title,
                "act": vol_act,
                "summaries": vol_summaries,
            })
            used_plot_elements.extend(sorted(vol_elements))
            if vol_hook:
                last_volume_hook = vol_hook
            introduced_characters.extend(sorted(vol_characters))

        yield {"type": "progress", "phase": "outline", "pct": 92, "label": "组装最终文档…"}

        # 组装完整 plan（使用分卷生成的结果）
        plan_outline = {
            "volumes": all_volumes,
            "total_chapters": chapter_counter,
            "three_act_map": f"第一幕·建置(约{sum(1 for v in all_volumes if '建置' in v.get('act',''))}卷) → 第二幕·对抗(约{sum(1 for v in all_volumes if '对抗' in v.get('act',''))}卷) → 第三幕·解决(约{sum(1 for v in all_volumes if '解决' in v.get('act',''))}卷)",
            "rhythm_notes": f"共{len(all_volumes)}卷{chapter_counter}章，每章约{chapter_words}字"
        }

        plan = {
            "title": wb.get("title", title or "未命名"),
            "genre": genre,
            "style": style_name,
            "target_words": target_words,
            "worldbuilding": wb.get("worldbuilding", {}),
            "characters": chars.get("characters", {}),
            "outline": plan_outline,
            "_meta": {
                "created_at": __import__("datetime").datetime.now().isoformat(),
                "model": self.model,
                "creative_input": creative_input,
                "streamed": True,
                "fallback_count": fallback_count,
                "is_partial": fallback_count > 0,
            },
        }
        
        # 标准化
        for vol in plan["outline"]["volumes"]:
            if not isinstance(vol, dict):
                continue
            vol["number"] = int(vol.get("number", 1))
            for ch in vol.get("chapters", []):
                if isinstance(ch, dict):
                    ch["number"] = int(ch.get("number", 1))
                    ch["target_words"] = int(ch.get("target_words", 1500))
        plan["outline"]["total_chapters"] = chapter_counter
        plan["target_words"] = int(plan.get("target_words", 0))

        # ── v2: 大纲验证与自动修复 ──
        validation = self.validate_outline(plan["outline"])
        if validation["warnings"]:
            log.warning(f"Outline validation: {len(validation['warnings'])} warnings")
        if validation["issues"]:
            log.warning(f"Outline validation: {len(validation['issues'])} issues, auto-repairing...")
            plan["outline"] = self.repair_outline(plan["outline"])
        
        yield {"type": "progress", "phase": "done", "pct": 100, "label": "创作方案完成！"}
        yield {"type": "done", "plan": plan}

    async def _call_llm(self, prompt: str, phase: str, max_tokens: int = 4096, retry: bool = True) -> dict:
        """调用 LLM 并解析 JSON（线程池隔离 + 3次重试 + 指数退避 + 简化兜底）"""
        log.info(f"Planner phase [{phase}]: calling LLM...")
        
        def _sync_call(temp: float = 0.8, use_simple_prompt: bool = False):
            actual_prompt = prompt
            if use_simple_prompt:
                # 最终兜底：用最简指令要求输出纯 JSON
                actual_prompt = prompt + "\n\n⚠️ 直接输出纯JSON，不要```json标记，不要任何解释文字。"
            
            kwargs = dict(
                model=self.model,
                messages=[{"role": "user", "content": actual_prompt}],
                temperature=temp,
                max_tokens=max_tokens,
            )
            if "v4" in self.model:
                kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
            response = self.client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content
            return content, self._parse_json(content)
        
        # 重试策略：temp 递减 + 退避递增 + 最后一次用简化 prompt
        retry_plan = [
            (0.8, 1, False),   # 第一次：正常温度，等1s
            (0.4, 2, False),   # 第二次：低温度，等2s
            (0.2, 3, True),    # 第三次：最低温 + 简化prompt兜底，等3s
        ]
        
        last_error = None
        last_raw = ""
        last_partial = None  # 保存最后一次部分解析结果
        
        for attempt, (temp, delay, use_simple) in enumerate(retry_plan):
            try:
                content, result = await asyncio.to_thread(_sync_call, temp, use_simple)
                if result:
                    if attempt > 0:
                        log.info(f"Planner phase [{phase}]: OK on retry #{attempt}")
                    else:
                        log.info(f"Planner phase [{phase}]: OK")
                    return result
                
                # JSON 解析失败 — 尝试部分修复
                last_raw = content or ""
                last_error = f"JSON parse failed (attempt {attempt+1})"
                
                # 尝试提取部分有效数据 (如大纲只解析出部分卷)
                partial = self._parse_partial(content) if content else None
                if partial:
                    last_partial = partial
                    log.warning(f"Planner phase [{phase}]: partial parse got {len(str(partial))} chars")
                
                log.warning(f"Planner phase [{phase}]: {last_error}, retrying in {delay}s")
                
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                log.warning(f"Planner phase [{phase}]: {last_error}, retrying in {delay}s")
            
            if attempt < len(retry_plan) - 1:
                await asyncio.sleep(delay)
        
        # ── 降级策略1: 尝试使用部分解析结果 ──
        if last_partial:
            log.warning(f"Planner phase [{phase}]: using partial parse as fallback")
            return last_partial
        
        # ── 降级策略2: 尝试从原始文本暴力提取 ──
        if last_raw:
            forced = self._force_extract(last_raw)
            if forced:
                log.warning(f"Planner phase [{phase}]: using force-extracted result")
                return forced
        
        # 全部失败
        log.error(f"Planner phase [{phase}]: ALL RETRIES FAILED. Last error: {last_error}")
        if last_raw:
            log.error(f"Planner phase [{phase}]: Last raw output (first 200): {last_raw[:200]}")
            log.error(f"Planner phase [{phase}]: Last raw output (last 200): {last_raw[-200:]}")
        return None

    def _parse_json(self, content: str) -> dict:
        """Robust JSON extraction from LLM response (supports both objects and arrays)"""
        if not content:
            return None
        content = content.strip()
        
        # Remove markdown code fences
        if content.startswith("```"):
            lines = content.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            content = "\n".join(lines).strip()
        
        # Strategy 0: JSON array (for per-volume chapter prompts that return [{...}])
        if content.startswith("["):
            try:
                parsed = json.loads(content.replace(",}", "}").replace(",]", "]"))
                if isinstance(parsed, list) and len(parsed) > 0:
                    log.info(f"JSON array parsed: {len(parsed)} items")
                    return {"data": parsed, "chapters": parsed}
            except json.JSONDecodeError:
                pass
            # Try array extraction
            start = content.find("[")
            end = content.rfind("]")
            if start >= 0 and end > start:
                try:
                    parsed = json.loads(content[start:end + 1].replace(",}", "}").replace(",]", "]"))
                    if isinstance(parsed, list) and len(parsed) > 0:
                        log.info(f"JSON array extracted: {len(parsed)} items")
                        return {"data": parsed, "chapters": parsed}
                except json.JSONDecodeError:
                    pass
        
        # Strategy 1: direct parse
        result = self._try_parse(content)
        if result is not None:
            return result
        
        # Strategy 2: extract {...} boundaries and retry
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            result = self._try_parse(content[start:end + 1])
            if result is not None:
                return result
        
        # Strategy 3: find the outermost valid JSON object
        # Look for the largest {...} that parses successfully
        brace_count = 0
        best_start = -1
        best_end = -1
        for i, ch in enumerate(content):
            if ch == "{":
                if brace_count == 0:
                    best_start = i
                brace_count += 1
            elif ch == "}":
                brace_count -= 1
                if brace_count == 0:
                    best_end = i
                    result = self._try_parse(content[best_start:best_end + 1])
                    if result is not None:
                        return result
        
        log.error(f"JSON parse failed. First 200: {content[:200]}")
        log.error(f"JSON parse failed. Last 200: {content[-200:]}")
        return None
    
    @staticmethod
    def _try_parse(json_str: str) -> dict:
        """Try to parse JSON with cleanup (no regex — avoid stack overflow on large strings)"""
        # Remove trailing commas (common LLM mistake) — simple string replace, no regex
        cleaned = json_str.replace(",}", "}").replace(",]", "]")
        cleaned = cleaned.replace(", }", "}").replace(", ]", "]")
        cleaned = cleaned.replace(",\n}", "}").replace(",\n]", "]")
        cleaned = cleaned.replace(",\r\n}", "}").replace(",\r\n]", "]")
        cleaned = cleaned.replace(",  }", "}").replace(",  ]", "]")
        cleaned = cleaned.replace(",    }", "}").replace(",    ]", "]")
        
        try:
            parsed = json.loads(cleaned)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            pass
        
        try:
            parsed = json.loads(json_str)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            pass

        # Strategy 3: JSON repair — try auto-closing unclosed braces/brackets
        # Common LLM failure: JSON truncated, missing closing ] or }
        try:
            repaired = json_str.rstrip()
            # Count unclosed pairs
            braces = repaired.count("{") - repaired.count("}")
            brackets = repaired.count("[") - repaired.count("]")
            # Check for unclosed strings
            in_string = False
            prev = ''
            for ch in repaired:
                if ch == '"' and prev != '\\':
                    in_string = not in_string
                prev = ch
            if in_string:
                repaired += '"'
            # Close brackets first, then braces
            repaired += "]" * max(0, brackets)
            repaired += "}" * max(0, braces)
            if repaired != json_str:
                result = json.loads(repaired)
                if result and isinstance(result, dict):
                    log.info(f"JSON repair successful: added {braces} }}, {brackets} ]]")
                    return result
        except (json.JSONDecodeError, Exception):
            pass
        
        return None

    # ── 大纲验证与修复 (v2) ──

    @staticmethod
    def validate_outline(outline: dict) -> dict:
        """验证大纲结构完整性
        
        Returns:
            {"valid": bool, "issues": [...], "warnings": [...]}
        """
        issues = []
        warnings = []
        
        if not outline:
            return {"valid": False, "issues": ["大纲为空"], "warnings": []}
        
        volumes = outline.get("volumes", [])
        if not volumes:
            return {"valid": False, "issues": ["大纲缺少卷结构"], "warnings": []}
        
        # 检查章节连续性
        prev_num = 0
        total = 0
        for vol in volumes:
            chapters = vol.get("chapters", [])
            if not chapters:
                warnings.append(f"第{vol.get('number','?')}卷没有章节")
                continue
            
            for ch in chapters:
                if not isinstance(ch, dict):
                    continue
                num = int(ch.get("number", 0))
                total += 1
                if prev_num > 0 and num != prev_num + 1:
                    issues.append(f"章节号跳跃: {prev_num}→{num}")
                prev_num = num
            
            # 检查必需字段
            for ch in chapters:
                if not isinstance(ch, dict):
                    continue
                if not ch.get("summary"):
                    warnings.append(f"第{ch.get('number','?')}章缺少摘要")
                if not ch.get("title"):
                    ch["title"] = f"第{ch.get('number','?')}章"
        
        # 检查总章节数一致性
        if outline.get("total_chapters", 0) != total:
            outline["total_chapters"] = total
            warnings.append(f"total_chapters 不匹配，已自动修正为 {total}")
        
        # 检查三幕结构
        acts = set(v.get("act", "") for v in volumes)
        has_acts = any("建置" in a or "对抗" in a or "解决" in a for a in acts)
        if len(volumes) >= 3 and not has_acts:
            warnings.append("多卷大纲缺少三幕标注")
        
        return {
            "valid": len(issues) == 0,
            "issues": issues,
            "warnings": warnings,
            "total_chapters": total,
            "volume_count": len(volumes),
        }

    @staticmethod
    def repair_outline(outline: dict) -> dict:
        """自动修复大纲常见问题"""
        if not outline:
            return outline
        
        volumes = outline.get("volumes", [])
        
        counter = 0
        for vol in volumes:
            if not isinstance(vol, dict):
                continue
            for ch in vol.get("chapters", []):
                if not isinstance(ch, dict):
                    continue
                counter += 1
                ch["number"] = counter
                if "target_words" not in ch:
                    ch["target_words"] = 1500
                if "emotion_curve" not in ch:
                    ch["emotion_curve"] = "平稳→起伏→悬念"
                if "characters" not in ch:
                    ch["characters"] = ["主角"]
                if "conflict" not in ch:
                    ch["conflict"] = ""
                if "hook" not in ch:
                    ch["hook"] = ""
        
        outline["total_chapters"] = counter
        
        for i, vol in enumerate(volumes):
            if not isinstance(vol, dict):
                continue
            if "number" not in vol:
                vol["number"] = i + 1
            if "act" not in vol:
                vol["act"] = ""
            if "theme" not in vol:
                vol["theme"] = ""
        
        return outline

    def _parse_partial(self, content: str) -> dict:
        """尝试从部分损坏的 JSON 中提取有效数据"""
        if not content:
            return None
        
        vol_start = content.find('"volumes"')
        if vol_start < 0:
            return None
        
        remaining = content[vol_start:]
        pos = remaining.find("[")
        if pos < 0:
            return None
        
        remaining = remaining[pos + 1:]
        volumes = []
        
        while remaining.strip():
            brace_count = 0
            obj_end = -1
            for i, ch in enumerate(remaining):
                if ch == "{":
                    brace_count += 1
                elif ch == "}":
                    brace_count -= 1
                    if brace_count == 0:
                        obj_end = i + 1
                        break
            
            if obj_end < 0:
                break
            
            obj_str = remaining[:obj_end]
            try:
                vol_obj = json.loads(obj_str.replace(",}", "}").replace(",]", "]"))
                volumes.append(vol_obj)
            except json.JSONDecodeError:
                pass
            
            remaining = remaining[obj_end:]
            while remaining and remaining[0] in ", \t\n\r":
                remaining = remaining[1:]
        
        if volumes:
            log.info(f"Partial parse: recovered {len(volumes)} volumes from truncated JSON")
            return {"volumes": volumes, "total_chapters": sum(
                len(v.get("chapters", [])) for v in volumes
            )}
        return None

    def _force_extract(self, raw_text: str) -> dict:
        """从完全无法解析的 LLM 输出中暴力提取大纲结构（最后兜底）"""
        if not raw_text:
            return None
        
        volumes = []
        current_vol = None
        
        for line in raw_text.split("\n"):
            line = line.strip()
            if not line:
                continue
            
            vol_match = re.search(r"第\s*(\d+)\s*卷\s*[：:\s]*(.+)", line)
            if vol_match:
                if current_vol and current_vol.get("chapters"):
                    volumes.append(current_vol)
                current_vol = {
                    "number": int(vol_match.group(1)),
                    "title": vol_match.group(2).strip().strip("「」\"\"'"),
                    "act": "", "theme": "", "act_function": "",
                    "chapters": [],
                }
                continue
            
            ch_match = re.search(r"第\s*(\d+)\s*章\s*[：:\s]*(.+)", line)
            if ch_match and current_vol:
                ch = {
                    "number": int(ch_match.group(1)),
                    "title": ch_match.group(2).strip()[:20],
                    "summary": ch_match.group(2).strip()[:50],
                    "emotion_curve": "平稳→起伏→悬念",
                    "conflict": "",
                    "characters": ["主角"],
                    "hook": "",
                    "target_words": 1500,
                }
                current_vol["chapters"].append(ch)
        
        if current_vol and current_vol.get("chapters"):
            volumes.append(current_vol)
        
        if volumes:
            log.info(f"Force extract: recovered {len(volumes)} volumes from unstructured text")
            return {
                "volumes": volumes,
                "total_chapters": sum(len(v.get("chapters", [])) for v in volumes),
                "three_act_map": "",
                "rhythm_notes": "（自动从文本提取）",
            }
        return None
