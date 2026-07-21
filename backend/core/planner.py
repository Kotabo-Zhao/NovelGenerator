"""NovelGenerator — Planner: 世界观、角色、大纲生成"""
import asyncio
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

        # 获取风格模板（支持自定义风格）
        if style_name.startswith("自定义") or style_name == "自定义风格":
            style_config = build_custom_style(style_name)
        else:
            style_config = get_style(style_name)
        
        style_guide = build_style_prompt(style_config)
        structure_hint = style_config.get("structure",
            "遵循「三章一小高潮、五章一中高潮、一卷一大高潮」的节奏")

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
{f'【书名】{title}' if title else '【书名】请根据创意自动生成一个有吸引力的书名'}

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
            "target_words": 3000
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

重要：确保 JSON 是有效的（注意逗号、引号、括号匹配），不要包含注释。
大纲的卷结构和节奏必须严格匹配「{style_config['name']}」的风格要求。"""

        log.info(f"Planning novel: {genre}/{style_name} - {inspiration[:50]}...")
        
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.8,
            max_tokens=8192,
        )

        content = response.choices[0].message.content
        plan = self._parse_json(content)
        
        if plan:
            # 标准化章节号（DeepSeek 可能返回字符串 "1" 而非整数 1）
            for vol in plan.get("outline", {}).get("volumes", []):
                vol["number"] = int(vol.get("number", 1))
                for ch in vol.get("chapters", []):
                    ch["number"] = int(ch.get("number", 1))
                    ch["target_words"] = int(ch.get("target_words", 3000))
            plan["outline"]["total_chapters"] = int(plan.get("outline", {}).get("total_chapters", 0))
            plan["target_words"] = int(plan.get("target_words", 0))
            plan["style"] = style_name  # 保存风格名
            
            plan["_meta"] = {
                "created_at": __import__("datetime").datetime.now().isoformat(),
                "model": self.model,
                "creative_input": creative_input,
            }
            log.info(f"Plan generated: {plan.get('title', 'Unknown')} — "
                     f"{plan.get('outline', {}).get('total_chapters', 0)} chapters")
        
        return plan

    async def plan_stream(self, creative_input: dict):
        """3阶段流式规划 — 前端可显示进度条
        
        Yields:
            {"type":"progress","phase":"worldbuilding","pct":25,"label":"构建世界观…"}
            {"type":"progress","phase":"characters","pct":55,"label":"设计角色…"}
            {"type":"progress","phase":"outline","pct":85,"label":"生成大纲…"}
            {"type":"done","plan":{...}}
        """
        genre = creative_input.get("genre", "玄幻")
        style_name = creative_input.get("style", "热血爽文")
        inspiration = creative_input.get("inspiration", "")
        target_words = creative_input.get("target_words", 500000)
        title = creative_input.get("title", "")

        if style_name.startswith("自定义") or style_name == "自定义风格":
            style_config = build_custom_style(style_name)
        else:
            style_config = get_style(style_name)
        
        style_guide = build_style_prompt(style_config)
        structure_hint = style_config.get("structure",
            "遵循「三章一小高潮、五章一中高潮、一卷一大高潮」的节奏")

        yield {"type": "progress", "phase": "start", "pct": 5, "label": "分析创意…"}

        # ── Phase 1: 世界观 (5% → 30%) ──
        yield {"type": "progress", "phase": "worldbuilding", "pct": 10, "label": "构建世界观体系…"}
        
        wb_prompt = f"""你是一位世界观架构师。请根据以下创意生成世界设定。

风格: {style_config['name']} ({style_config['author']})
创意: {inspiration}
题材: {genre}

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
            yield {"type": "error", "message": "世界观生成失败"}
            return

        yield {"type": "progress", "phase": "worldbuilding", "pct": 30, "label": "世界观完成 ✓"}

        # ── Phase 2: 角色 (30% → 55%) ──
        yield {"type": "progress", "phase": "characters", "pct": 32, "label": "设计角色关系网…"}
        
        char_prompt = f"""你是一位角色设计师。请根据以下世界观为小说创作角色体系。

世界观: {json.dumps(wb.get('worldbuilding',{}), ensure_ascii=False)[:500]}
风格: {style_config['name']}
创意: {inspiration}

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
            yield {"type": "error", "message": "角色生成失败"}
            return

        yield {"type": "progress", "phase": "characters", "pct": 55, "label": "角色设计完成 ✓"}

        # ── Phase 3: 大纲 (55% → 95%) ──
        yield {"type": "progress", "phase": "outline", "pct": 58, "label": "规划章节大纲…"}

        outline_prompt = f"""你是小说大纲规划师。仅根据以下设定生成章节大纲。不要输出世界观和角色。

世界观: {json.dumps(wb.get('worldbuilding',{}), ensure_ascii=False)[:400]}
主角名: {chars.get('characters',{}).get('protagonist',{}).get('name','主角')}
题材: {genre}  风格: {style_config['name']}  创意: {inspiration}  目标: {target_words}字
节奏: {structure_hint}

【重要】只输出 JSON，且只包含 "outline" 字段。每章摘要控制在30字内。
```json
{{
  "outline": {{
    "volumes": [
      {{
        "number":1,"title":"","act":"第一幕·建置","theme":"","act_function":"",
        "chapters":[
          {{"number":1,"title":"","summary":"30字内核心事件","emotion_curve":"","conflict":"","characters":[""],"hook":"","target_words":3000}}
        ]
      }}
    ],
    "total_chapters":0,"three_act_map":"","rhythm_notes":""
  }}
}}
```"""
        outline = await self._call_llm(outline_prompt, "outline", max_tokens=16384)
        if not outline:
            yield {"type": "error", "message": "大纲生成失败"}
            return

        yield {"type": "progress", "phase": "outline", "pct": 92, "label": "组装最终文档…"}

        # 组装完整 plan
        plan = {
            "title": wb.get("title", title or "未命名"),
            "genre": genre,
            "style": style_name,
            "target_words": target_words,
            "worldbuilding": wb.get("worldbuilding", {}),
            "characters": chars.get("characters", {}),
            "outline": outline.get("outline", {}),
            "_meta": {
                "created_at": __import__("datetime").datetime.now().isoformat(),
                "model": self.model,
                "creative_input": creative_input,
                "streamed": True,
            },
        }
        
        # 标准化
        for vol in plan.get("outline", {}).get("volumes", []):
            vol["number"] = int(vol.get("number", 1))
            for ch in vol.get("chapters", []):
                ch["number"] = int(ch.get("number", 1))
                ch["target_words"] = int(ch.get("target_words", 3000))
        plan["outline"]["total_chapters"] = int(plan.get("outline", {}).get("total_chapters", 0))
        plan["target_words"] = int(plan.get("target_words", 0))

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
            (0.8, 2, False),   # 第一次：正常温度，等2s
            (0.4, 4, False),   # 第二次：低温度，等4s
            (0.2, 8, True),    # 第三次：最低温 + 简化prompt兜底，等8s
        ]
        
        last_error = None
        last_raw = ""
        
        for attempt, (temp, delay, use_simple) in enumerate(retry_plan):
            try:
                content, result = await asyncio.to_thread(_sync_call, temp, use_simple)
                if result:
                    if attempt > 0:
                        log.info(f"Planner phase [{phase}]: OK on retry #{attempt}")
                    else:
                        log.info(f"Planner phase [{phase}]: OK")
                    return result
                
                # JSON 解析失败
                last_raw = content or ""
                last_error = f"JSON parse failed (attempt {attempt+1})"
                log.warning(f"Planner phase [{phase}]: {last_error}, retrying in {delay}s")
                
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                log.warning(f"Planner phase [{phase}]: {last_error}, retrying in {delay}s")
            
            if attempt < len(retry_plan) - 1:
                await asyncio.sleep(delay)
        
        # 全部重试失败
        log.error(f"Planner phase [{phase}]: ALL RETRIES FAILED. Last error: {last_error}")
        if last_raw:
            log.error(f"Planner phase [{phase}]: Last raw output (first 200): {last_raw[:200]}")
            log.error(f"Planner phase [{phase}]: Last raw output (last 200): {last_raw[-200:]}")
        return None

    def _parse_json(self, content: str) -> dict:
        """Robust JSON extraction from LLM response"""
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
        """Try to parse JSON with cleanup (protected against recursion)"""
        import re
        
        # Skip regex for very large strings (>50K chars)
        if len(json_str) < 50000:
            try:
                # Remove trailing commas (common LLM mistake)
                cleaned = re.sub(r',(\s*[}\]])', r'\1', json_str)
                try:
                    return json.loads(cleaned)
                except json.JSONDecodeError:
                    pass
            except RecursionError:
                log.warning("Regex recursion avoided, using bare parse")
        
        # Bare parse attempt
        try:
            return json.loads(json_str)
        except (json.JSONDecodeError, RecursionError):
            pass
        
        return None
