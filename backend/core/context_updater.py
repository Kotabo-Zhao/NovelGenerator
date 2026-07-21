"""NovelGenerator — Global Context Updater: 全局状态追踪 Agent

职责: 每章完成后更新角色状态、力量等级、位置、势力关系
参考: AI_Gen_Novel 的 GlobalContextUpdater
"""

import json
import os
import logging
from openai import OpenAI

log = logging.getLogger(__name__)

CU_SYSTEM = """你是一位细心的记录员，负责追踪小说中所有角色和世界状态的变化。

## 你的任务

阅读刚写完的章节，提取以下信息更新状态文件：

1. **角色状态变化**: 谁出现了？状态有什么变化？（受伤/晋级/死亡/新的关系）
2. **力量体系变化**: 主角/配角的等级变化、新技能获取
3. **关键位置**: 本章发生在哪？有哪些新地点？
4. **势力变化**: 势力之间的关系有变化吗？
5. **关键物品**: 获得了什么重要物品？失去了什么？

## 输出格式

返回 JSON:
```json
{
  "chapter": 章节号,
  "summary": "本章一句话摘要",
  "character_updates": {"角色名": "变化描述"},
  "power_updates": {"角色名": "等级/技能变化"},
  "locations": ["新地点或重要地点"],
  "faction_updates": "势力关系变化",
  "key_items": ["重要物品"],
  "hooks_planted": ["本章新埋的伏笔"],
  "hooks_resolved": ["本章回收的伏笔"]
}
```

只输出 JSON，不要其他内容。"""


class ContextUpdater:
    """全局状态追踪 — 确保长篇一致性"""

    def __init__(self, client: OpenAI, model: str):
        self.client = client
        self.model = model

    def update(self, novel_id: str, chapter_num: int, chapter_content: str,
               current_state: dict) -> dict:
        """分析新章节，更新全局状态
        
        Args:
            novel_id: 小说ID
            chapter_num: 章节号
            chapter_content: 本章正文
            current_state: 当前全局状态
        Returns:
            更新后的状态字典
        """
        # 截取章节前2000字分析（节省token）
        snippet = chapter_content[:2000] if len(chapter_content) > 2000 else chapter_content
        
        state_text = json.dumps(current_state, ensure_ascii=False, indent=2) if current_state else "{}"
        
        user_prompt = f"""请分析第{chapter_num}章，更新状态。

当前状态:
{state_text}

本章内容片段:
{snippet}

请提取本章的状态变化并输出 JSON。"""

        log.info(f"ContextUpdater: chapter {chapter_num}")
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": CU_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=1024,
                response_format={"type": "json_object"},
            )
            
            content = response.choices[0].message.content
            updates = json.loads(content)
            
            # 合并到当前状态
            if not current_state:
                current_state = {"characters": {}, "power_levels": {}, "locations": [], "factions": {}, "items": [], "chapters_summary": {}}
            
            # 合并角色更新
            for name, change in updates.get("character_updates", {}).items():
                if name not in current_state.setdefault("characters", {}):
                    current_state["characters"][name] = []
                current_state["characters"][name].append(f"[第{chapter_num}章] {change}")
            
            # 合并力量更新
            for name, change in updates.get("power_updates", {}).items():
                current_state.setdefault("power_levels", {})[name] = f"[第{chapter_num}章] {change}"
            
            # 合并位置
            for loc in updates.get("locations", []):
                if loc not in current_state.setdefault("locations", []):
                    current_state["locations"].append(loc)
            
            # 章节摘要
            current_state.setdefault("chapters_summary", {})[str(chapter_num)] = updates.get("summary", "")
            
            log.info(f"ContextUpdater: state updated for chapter {chapter_num}")
            return current_state
            
        except Exception as e:
            log.error(f"ContextUpdater failed: {e}")
            return current_state

    def get_context_for_chapter(self, novel_id: str, chapter_num: int, memory) -> str:
        """为写作提供全局上下文"""
        state_path = os.path.join(memory.get_novel_dir(novel_id), "global_state.json")
        if not os.path.exists(state_path):
            return ""
        
        with open(state_path, "r", encoding="utf-8") as f:
            state = json.load(f)
        
        parts = ["## 全局状态\n"]
        
        # 角色状态
        chars = state.get("characters", {})
        if chars:
            parts.append("### 角色状态")
            for name, changes in list(chars.items())[:8]:
                recent = changes[-2:] if len(changes) > 2 else changes
                parts.append(f"- {name}: {'; '.join(recent)}")
        
        # 力量等级
        powers = state.get("power_levels", {})
        if powers:
            parts.append("\n### 当前力量等级")
            for name, level in powers.items():
                parts.append(f"- {name}: {level}")
        
        # 近期摘要
        summaries = state.get("chapters_summary", {})
        recent_chs = sorted(int(k) for k in summaries.keys() if int(k) >= chapter_num - 3)
        if recent_chs:
            parts.append("\n### 近3章摘要")
            for ch in recent_chs:
                parts.append(f"- 第{ch}章: {summaries.get(str(ch), '')}")
        
        return "\n".join(parts)
