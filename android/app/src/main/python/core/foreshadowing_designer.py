"""NovelGenerator — Foreshadowing Designer: 伏笔设计师 Agent

职责: 在创作阶段主动规划伏笔的埋设和回收时机
参考: AI_Gen_Novel 的 Foreshadowing Designer Agent
"""

import json
import logging
from openai import OpenAI

log = logging.getLogger(__name__)

FD_SYSTEM = """你是一位悬疑小说大师，专精于伏笔设计和回收。

## 你的任务

分析小说大纲，找出可以埋设伏笔的关键节点，规划何时埋下、何时揭开。

## 伏笔设计原则

1. **分布均匀**: 不要集中在某几章，全书均匀分布
2. **层次递进**: 小伏笔3-5章回收，中伏笔10-20章回收，大伏笔跨卷回收
3. **多类型**: 人物伏笔（身份/动机）、物品伏笔（道具/法宝）、事件伏笔（预言/传说）、对话伏笔（一句话在后期被重新理解）
4. **自然感**: 伏笔要融入剧情，不能太刻意
5. **回收满足感**: 回收时要让读者有「原来如此！」的体验

## 输出格式

返回 JSON 数组，每个伏笔格式:
```json
[
  {
    "description": "伏笔描述",
    "type": "character/object/event/dialogue",
    "plant_chapter": 埋设章节号,
    "reveal_chapter": 回收章节号,
    "importance": "high/medium/low",
    "plant_hint": "如何在当前章节自然地埋下这个伏笔的建议"
  }
]
```

只输出 JSON，不要其他内容。"""


class ForeshadowingDesigner:
    """伏笔设计师 — 主动规划和追踪伏笔生命周期"""

    def __init__(self, client: OpenAI, model: str):
        self.client = client
        self.model = model

    def design(self, plan: dict, target_count: int = 5) -> list:
        """分析大纲，规划伏笔
        
        Args:
            plan: 小说规划数据 (含 outline)
            target_count: 目标伏笔数量
        Returns:
            [{description, type, plant_chapter, reveal_chapter, importance, plant_hint}]
        """
        total_chapters = plan.get("outline", {}).get("total_chapters", 30)
        
        # 提取大纲摘要
        outline_summary = []
        for vol in plan.get("outline", {}).get("volumes", []):
            vol_info = f"第{vol['number']}卷「{vol.get('title','')}」: {vol.get('theme','')}"
            ch_list = []
            for ch in vol.get("chapters", [])[:5]:
                ch_list.append(f"  第{ch['number']}章「{ch.get('title','')}」: {ch.get('summary','')[:40]}")
            outline_summary.append(vol_info + "\n" + "\n".join(ch_list))
        
        outline_text = "\n\n".join(outline_summary)
        
        user_prompt = f"""请为以下小说大纲设计 {target_count} 个伏笔。

总章节数: {total_chapters}

大纲:
{outline_text}

请规划每个伏笔的埋设和回收时机。确保分布均匀，类型多样。"""

        log.info(f"ForeshadowingDesigner: designing {target_count} hooks")
        
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": FD_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.7,
            max_tokens=2048,
            response_format={"type": "json_object"},
        )

        try:
            content = response.choices[0].message.content
            hooks = json.loads(content)
            if isinstance(hooks, dict):
                hooks = hooks.get("foreshadowing", hooks.get("hooks", []))
            log.info(f"ForeshadowingDesigner: {len(hooks)} hooks designed")
            return hooks[:target_count]
        except Exception as e:
            log.error(f"ForeshadowingDesigner failed: {e}")
            return []
