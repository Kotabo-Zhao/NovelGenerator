"""NovelGenerator — CharacterStateTracker: 角色状态追踪系统

核心理念：
  角色状态、故事线、生成内容三方联动。
  每次生成前查询当前状态，每次生成后自动提取状态变化。

数据结构（存储在 global_state.json 的扩展字段中）：
  protagonist_state: 主角完整状态快照（身份/修为/声望/装备/健康/成就）
  active_characters: 活跃配角的状态快照
  information_spread: 信息传播追踪（谁知道了什么）
  storyline_position: 故事线定位

用法：
  tracker = CharacterStateTracker(client, model, memory)
  # 初始化
  tracker.init_from_plan(novel_id)
  # 提取更新
  await tracker.update_from_chapter(novel_id, chapter_num, chapter_text)
  # 构建上下文
  context_text = tracker.build_context(novel_id)
"""

import json
import logging
from typing import Optional

log = logging.getLogger(__name__)


# ── 提取 prompt ──

EXTRACT_PROMPT = """你是一个角色状态追踪器。阅读以下章节正文，分析每个出场角色的状态变化。

## 提取规则
1. 只报告有变化的项。没有变化就不要包含该键
2. 主角用「protagonist」键，其他角色用「other_characters」键
3. 信息传播追踪：如果一个值得注意的事件（主角的成就/秘密）被新的角色知道了，或传播到了新的区域，记录到 information_spread
4. 时间推进：估计本章经过了多少时间（分钟/小时/天）

## 当前已知状态（供参考）
{current_state}

## 新章节正文
{chapter_text}

## 输出格式（JSON only）
```json
{{
  "protagonist": {{
    "identity_change": "新身份（如有）",
    "cultivation_change": {{"stage": "新境界", "new_ability": "新技能"}},
    "reputation_change": {{"event": "触发事件", "effect": "上升/下降", "new_level": "新声望等级名"}},
    "location_change": "移动到的新地点",
    "equipment_changes": {{"gained": ["获得"], "lost": ["失去"]}},
    "health_change": "受伤/痊愈/恶化·具体描述",
    "new_achievement": "值得记录的成就",
    "new_relationships": {{"角色名": {{"type": "战友/盟友/敌人/导师", "trust": 0-100}}}}
  }},
  "other_characters": {{
    "角色名": {{
      "appeared": true,
      "identity": "身份",
      "location": "出现位置",
      "health_change": "状态变化",
      "relationship_change": {{"target": "对象", "change": "变化描述"}}
    }}
  }},
  "information_spread": [
    {{"event": "值得传播的事件简述", "new_known_by": ["新知道的角色"], "spread_to": ["传播到的地区"]}}
  ],
  "story_progress": {{
    "arc_progress": "本弧进度·前期/中段/高潮/收尾",
    "time_passed": "本章经过的时间（如：2小时/半天/3天）"
  }}
}}
```

只输出 JSON。不要分析过程。"""


# ── 上下文构建模板 ──

CONTEXT_TEMPLATE = """## 👤 主角状态（本章开始时）

- 姓名: {name}
- 当前身份: {identity}
- 修为: {cultivation}
- 声望: {reputation}
- 位置: {location}
- 装备: {equipment}
- 健康状况: {health}
- 近期成就: {achievements}
"""


class CharacterStateTracker:
    """角色状态追踪器 — 负责状态提取、更新、注入"""

    def __init__(self, client, model: str, memory):
        """
        Args:
            client: OpenAI 客户端
            model: 模型名
            memory: SharedMemoryManager 实例
        """
        self.client = client
        self.model = model
        self.memory = memory

    # ── 初始化 ──

    def init_from_plan(self, novel_id: str):
        """从 plan.json 和 character_bible.json 初始化角色状态"""
        try:
            plan = self.memory.read("plan", novel_id)
            bible = self.memory.read("character_bible", novel_id)
            state = self.memory.read("global_state", novel_id) or {}

            # 如果已有 protagonist_state，不覆盖
            if state.get("protagonist_state"):
                return

            wb = plan.get("worldbuilding", {})
            chars = plan.get("characters", {})
            protagonist = chars.get("protagonist", {})

            state["protagonist_state"] = {
                "name": protagonist.get("name", "主角"),
                "identity": protagonist.get("identity", "待定"),
                "cultivation": protagonist.get("cheat", "") or wb.get("power_system", "待定"),
                "reputation": "无名小卒",
                "location": wb.get("geography", "待定"),
                "equipment": [],
                "health": "良好",
                "achievements": [],
            }

            # 从 character_bible 提取配角
            active_chars = {}
            for c in chars.get("supporting", [])[:8]:
                name = c.get("name", "")
                if name:
                    active_chars[name] = {
                        "name": name,
                        "identity": c.get("identity", ""),
                        "cultivation": "",
                        "location": "待定",
                        "status": "活",
                        "last_appeared": 0,
                        "relationship": c.get("relation", ""),
                    }
            state["active_characters"] = active_chars

            state["information_spread"] = []
            state["storyline_position"] = {
                "current_arc": "",
                "arc_progress": "前期",
                "major_events": [],
                "timeline_days": 0,
            }

            self.memory.write("global_state", novel_id, state)
            log.info(f"Character states initialized for {novel_id}")

        except Exception as e:
            log.warning(f"Character state init failed (non-fatal): {e}")

    # ── 提取更新 ──

    async def update_from_chapter(self, novel_id: str, chapter_num: int, chapter_text: str):
        """从章节正文提取状态变化并更新 global_state.json"""
        try:
            state = self.memory.read("global_state", novel_id) or {}
            current_summary = json.dumps({
                "protagonist": state.get("protagonist_state", {}),
                "active_characters": {k: v for k, v in
                    list(state.get("active_characters", {}).items())[:5]},
                "storyline": state.get("storyline_position", {}),
            }, ensure_ascii=False, indent=2)

            prompt = EXTRACT_PROMPT.format(
                current_state=current_summary,
                chapter_text=chapter_text[:8000],  # 限制长度控制成本
            )

            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是角色状态追踪器。只输出JSON。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=1500,
            )

            extracted = json.loads(resp.choices[0].message.content.strip())

            # ── 合并更新 ──

            # 主角状态
            proto = state.get("protagonist_state", {})
            p_update = extracted.get("protagonist", {})
            if p_update:
                if p_update.get("identity_change"):
                    proto["identity"] = p_update["identity_change"]
                if p_update.get("cultivation_change"):
                    cu = p_update["cultivation_change"]
                    proto["cultivation"] = cu.get("stage", proto.get("cultivation", ""))
                    if cu.get("new_ability"):
                        proto["cultivation"] += f" + {cu['new_ability']}"
                if p_update.get("reputation_change"):
                    rp = p_update["reputation_change"]
                    old_reputation = proto.get("reputation", "无名小卒")
                    proto["reputation"] = rp.get("new_level", old_reputation)
                if p_update.get("location_change"):
                    proto["location"] = p_update["location_change"]
                if p_update.get("equipment_changes"):
                    eq = p_update["equipment_changes"]
                    proto["equipment"] = list(dict.fromkeys(
                        proto.get("equipment", []) + eq.get("gained", [])
                    ))
                    for lost in eq.get("lost", []):
                        if lost in proto.get("equipment", []):
                            proto["equipment"].remove(lost)
                if p_update.get("health_change"):
                    proto["health"] = p_update["health_change"]
                if p_update.get("new_achievement"):
                    proto.setdefault("achievements", []).append(
                        {"chapter": chapter_num, "event": p_update["new_achievement"]}
                    )
                if p_update.get("new_relationships"):
                    for name, rel in p_update["new_relationships"].items():
                        proto.setdefault("relationships", {})[name] = rel
                state["protagonist_state"] = proto

            # 配角状态
            active = state.get("active_characters", {})
            others = extracted.get("other_characters", {})
            for name, info in others.items():
                if not isinstance(info, dict):
                    continue
                if name not in active:
                    active[name] = {"name": name}
                char = active[name]
                char["last_appeared"] = chapter_num
                for field in ["identity", "location", "cultivation"]:
                    if info.get(field):
                        char[field] = info[field]
                if info.get("health_change"):
                    char["status"] = info["health_change"]
                if info.get("relationship_change"):
                    rel = info["relationship_change"]
                    char.setdefault("relationship_changes", []).append(
                        {"chapter": chapter_num, **rel}
                    )
            state["active_characters"] = active

            # 信息传播
            spreads = extracted.get("information_spread", [])
            if spreads:
                state.setdefault("information_spread", []).extend(spreads)
                # 去重: 保留最近 30 条
                state["information_spread"] = state["information_spread"][-30:]

            # 故事线进度
            story = extracted.get("story_progress", {})
            if story:
                sp = state.get("storyline_position", {})
                if story.get("arc_progress"):
                    sp["arc_progress"] = story["arc_progress"]
                if story.get("time_passed"):
                    time_str = story["time_passed"]
                    # 简单时间累加
                    import re
                    days_match = re.search(r'(\d+)\s*天', time_str)
                    hours_match = re.search(r'(\d+)\s*小时', time_str)
                    minutes_match = re.search(r'(\d+)\s*分钟', time_str)
                    days = float(days_match.group(1)) if days_match else 0
                    hours = float(hours_match.group(1)) if hours_match else 0
                    minutes = float(minutes_match.group(1)) if minutes_match else 0
                    total = days + hours / 24 + minutes / 1440
                    sp["timeline_days"] = round(sp.get("timeline_days", 0) + total, 1)

                if extracted.get("protagonist", {}).get("new_achievement"):
                    sp.setdefault("major_events", []).append({
                        "chapter": chapter_num,
                        "event": extracted["protagonist"]["new_achievement"],
                    })
                state["storyline_position"] = sp

            self.memory.write("global_state", novel_id, state)
            log.info(f"Character states updated from chapter {chapter_num}: "
                    f"proto={len(p_update)} fields, others={len(others)} chars, "
                    f"spreads={len(spreads)}, story={bool(story)}")

        except json.JSONDecodeError as e:
            log.warning(f"State extraction JSON parse failed (non-fatal): {e}")
        except Exception as e:
            log.warning(f"State extraction failed (non-fatal): {e}")

    # ── 上下文构建 ──

    def build_context(self, novel_id: str) -> str:
        """构建当前角色状态的文本上下文"""
        try:
            state = self.memory.read("global_state", novel_id) or {}
            proto = state.get("protagonist_state", {})
            if not proto:
                return ""

            achievements = proto.get("achievements", [])
            ach_text = "、".join([a.get("event", str(a)) for a in achievements[-5:]]) if achievements else "无"

            return CONTEXT_TEMPLATE.format(
                name=proto.get("name", "主角"),
                identity=proto.get("identity", "未知"),
                cultivation=proto.get("cultivation", "未知"),
                reputation=proto.get("reputation", "无名小卒"),
                location=proto.get("location", "未知"),
                equipment="、".join(proto.get("equipment", [])) if proto.get("equipment") else "无特殊装备",
                health=proto.get("health", "良好"),
                achievements=ach_text,
            )
        except Exception as e:
            log.warning(f"Character state context build failed: {e}")
            return ""
