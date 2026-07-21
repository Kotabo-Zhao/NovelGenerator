"""NovelGenerator — Planner: 世界观、角色、大纲生成"""
import json
import logging
from openai import OpenAI
from .styles import get_style, build_style_prompt, build_custom_style

log = logging.getLogger(__name__)

PLANNER_SYSTEM = """你是一位资深的小说策划编辑，专精于网文和类型小说的世界观搭建、角色设计和大纲规划。

你的任务是根据用户提供的创意，生成结构化的设定文档。你必须严格按照 JSON 格式输出。

## 目标风格

{style_guide}

## 输出要求

### 世界观 (worldbuilding)
- 时代背景、地理环境、社会结构
- 力量体系/科技水平（如有）
- 核心冲突（主线矛盾）
- 势力分布（3-5个势力/组织）

### 角色 (characters)
- 主角：姓名、年龄、身份、性格、外貌、背景故事、核心动机、金手指/特殊能力
- 配角：3-5个，每人包含姓名、身份、与主角关系、性格特征、在主线中的作用
- 反派：1-2个，包含动机、实力、与主角的冲突点

### 大纲 (outline)
- 卷结构：3-5卷，每卷一个主题
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
      "personality": "性格特征",
      "appearance": "外貌描述",
      "backstory": "背景故事",
      "motivation": "核心动机",
      "cheat": "金手指/特殊能力",
      "arc": "角色成长弧线"
    }},
    "supporting": [
      {{"name": "", "identity": "", "relation": "", "personality": "", "role": ""}}
    ],
    "antagonist": [
      {{"name": "", "motivation": "", "power": "", "conflict": ""}}
    ]
  }},
  "outline": {{
    "volumes": [
      {{
        "number": 1,
        "title": "卷标题",
        "theme": "本卷主题",
        "chapters": [
          {{
            "number": 1,
            "title": "章标题",
            "summary": "核心事件一句话描述",
            "scene_type": "动作场景/情感场景/对话场景/混合",
            "emotion_curve": "压抑→爆发→余韵（本章情绪弧线）",
            "conflict": "冲突描述与类型[IN内心/IR人际/EN环境/DE宿命]:强度1-5",
            "characters": ["出场角色"],
            "hook": "本章结尾钩子（余韵态具体内容）",
            "target_words": 3000
          }}
        ]
      }}
    ],
    "total_chapters": 0,
    "rhythm_notes": "节奏设计说明"
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

    def _parse_json(self, content: str) -> dict:
        """Extract JSON from LLM response (handle markdown code blocks)"""
        content = content.strip()
        
        # Remove markdown code fences
        if content.startswith("```"):
            lines = content.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            content = "\n".join(lines)

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            start = content.find("{")
            end = content.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(content[start:end + 1])
                except json.JSONDecodeError:
                    pass
            log.error(f"Failed to parse JSON from response (first 500 chars): {content[:500]}")
            return None
