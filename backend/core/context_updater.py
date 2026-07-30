"""NovelGenerator — Global Context Updater v3: 结构化角色状态追踪

职责:
1. 每章完成后从全文提取每个角色的精确状态
2. 写作前注入角色当前状态到上下文，防止行为逻辑断裂

角色快照 = {location, emotion, health, current_goal, equipment, relationships}
"""

import json
import os
import logging
import re
from openai import OpenAI
from .resilient_client import ResilientLLMClient

log = logging.getLogger(__name__)

# ── v3: 结构化角色快照 schema ──
CHARACTER_STATE_SCHEMA = {
    "location": "当前所在位置（具体地点，不是'待定'）",
    "emotion": "当前情绪状态（愤怒/平静/悲伤/恐惧/激动...）",
    "health": "身体状况（健康/轻伤/重伤/濒死/已死亡）+ 具体描述",
    "current_goal": "当前正在做什么/想去哪/想达成什么",
    "equipment": ["随身携带的关键物品"],
    "last_seen_chapter": 0,  # 最后出现的章节号
    "role": "protagonist/supporting/antagonist/minor",
    "identity": "身份描述",
}

CU_SYSTEM_V3 = """你是一位严谨的角色状态记录员。你的任务是读完整章小说后，为【本章出现的每个角色】提取精确的当前状态。

## 提取规则

对每个在本章中出现（哪怕只被提到一句）的角色，提取：

1. **location** (string): 这个角色在【本章结尾】时在哪里？要具体。如果本章没出现这个角色，保持上一次记录的位置不变。
2. **emotion** (string): 本章结尾时这个角色的情绪。用1-3个中文词描述。
3. **health** (string): 身体状况。必须包含一个状态标签（健康/轻伤/重伤/濒死/已死亡）+ 简短描述。例如"重伤 - 右臂骨折，失血过多"。
4. **current_goal** (string): 本章结尾时，这个角色接下来想做什么？
5. **equipment** (list): 本章中角色获得/持有的重要物品。失去的物品从列表移除。
6. **last_seen_chapter** (int): 本章章节号（如果本章出现了）。
7. **relationships** (object): 与其他角色的关系变化（格式: {"角色名": "关系描述"}）

## 重要原则

- **只更新本章出现或状态变化的角色**，未出现的角色不要编造状态
- 如果角色已死亡，health 必须标注 "已死亡"
- 位置必须精确到场景级别（"九霄城东门客栈二楼"而不是"九霄城"）
- 关系变化只记录【本章新发生】的变化

## 输出格式

只返回 JSON:
```json
{
  "chapter": 章节号,
  "summary": "本章一句话摘要",
  "characters": {
    "角色名": {
      "location": "...",
      "emotion": "...",
      "health": "...",
      "current_goal": "...",
      "equipment": [...],
      "last_seen_chapter": 数字,
      "relationships": {"其他角色": "关系描述"}
    }
  },
  "new_locations": ["本章新出现的地点"],
  "dead_characters": ["本章确认死亡的角色"],
  "timeline": {
    "days_elapsed": 本章经过的天数(估算),
    "chapter_start_time": "白天/傍晚/深夜/黎明"
  }
}
```

只输出 JSON，不要其他内容。"""


class ContextUpdater:
    """v3: 结构化角色状态追踪器"""

    def __init__(self, client: OpenAI, model: str):
        self.client = client
        self.model = model
        self._resilient = ResilientLLMClient(client, model)

    def update(self, novel_id: str, chapter_num: int, chapter_content: str,
               current_state: dict) -> dict:
        """分析完整章节，提取每个角色的结构化状态
        
        v3改进:
        - 读全文首尾各2500字（首部=开场，尾部=结尾状态）
        - 输出结构化角色快照而非文本列表
        """
        # v3: 首尾各取2500字覆盖全貌
        head_len = min(2500, len(chapter_content))
        tail_len = min(2500, len(chapter_content))
        head = chapter_content[:head_len]
        tail = chapter_content[-tail_len:] if len(chapter_content) > head_len + 500 else ""
        snippet = head + ("\n\n...(中间省略)...\n\n" + tail if tail else "")
        
        # 取已有的角色列表作为提示
        existing_chars = {}
        if isinstance(current_state.get("characters"), dict):
            existing_chars = current_state["characters"]
        char_names = json.dumps(list(existing_chars.keys())[:12], ensure_ascii=False) if existing_chars else "[]"
        
        user_prompt = f"""请分析第{chapter_num}章，更新每个角色的结构化状态。

已知角色列表: {char_names}

本章全文（首尾）:
{snippet[:5000]}

请为【本章中出现的每个角色】输出完整的当前状态。"""
        
        log.info(f"ContextUpdater v3: chapter {chapter_num} ({len(chapter_content)} chars)")
        
        # 防御性清洗 + 旧格式迁移
        if not isinstance(current_state, dict):
            current_state = {}
        if not isinstance(current_state.get("characters"), dict):
            current_state["characters"] = {}
        
        # v3: 旧格式迁移 — characters 从 name->list 升级为 name->dict
        migrated = {}
        for name, data in current_state["characters"].items():
            if isinstance(data, dict) and "location" in data:
                migrated[name] = data  # 已是新格式
            elif isinstance(data, list):
                # 旧格式: ["[第X章] 变化描述"] — 保留为 history
                migrated[name] = {
                    "location": "未知",
                    "emotion": "未知", 
                    "health": "未知",
                    "current_goal": "未知",
                    "equipment": [],
                    "last_seen_chapter": 0,
                    "relationships": {},
                    "_history": data,  # 旧数据保留为历史
                }
            elif isinstance(data, dict):
                # 部分新格式，补全字段
                data.setdefault("location", "未知")
                data.setdefault("emotion", "未知")
                data.setdefault("health", "未知")
                data.setdefault("current_goal", "未知")
                data.setdefault("equipment", [])
                data.setdefault("last_seen_chapter", 0)
                data.setdefault("relationships", {})
                migrated[name] = data
            else:
                migrated[name] = {
                    "location": "未知", "emotion": "未知", "health": "未知",
                    "current_goal": "未知", "equipment": [], "last_seen_chapter": 0,
                    "relationships": {},
                }
        current_state["characters"] = migrated
        
        # v3: 同步 plan 中的角色预设（主角、重要配角）
        self._merge_active_characters(current_state)
        
        try:
            response = self._resilient.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": CU_SYSTEM_V3},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                max_tokens=2048,
                response_format={"type": "json_object"},
            )
            
            content = response.choices[0].message.content
            updates = json.loads(content)
            
            # ── 合并角色状态 ──
            new_chars = updates.get("characters", {})
            for name, state in new_chars.items():
                if not isinstance(state, dict):
                    continue
                # 合并到已有状态（保留旧数据中未更新的字段）
                if name in current_state["characters"]:
                    old = current_state["characters"][name]
                    if isinstance(old, dict):
                        old.update(state)
                    else:
                        # 旧格式是 list → 升级为 dict
                        current_state["characters"][name] = state
                else:
                    current_state["characters"][name] = state
                
                # 确保关键字段存在
                char = current_state["characters"][name]
                char.setdefault("location", "未知")
                char.setdefault("emotion", "未知")
                char.setdefault("health", "未知")
                char.setdefault("current_goal", "未知")
                char.setdefault("equipment", [])
                char.setdefault("last_seen_chapter", chapter_num)
                char.setdefault("relationships", {})
            
            # ── 标记死亡角色 ──
            for dead_name in updates.get("dead_characters", []):
                if dead_name in current_state["characters"]:
                    current_state["characters"][dead_name]["health"] = "已死亡"
                    current_state["characters"][dead_name]["location"] = "（已死亡）"
                    current_state["characters"][dead_name]["current_goal"] = "（无）"
            
            # ── 合并位置 ──
            if "locations" not in current_state:
                current_state["locations"] = []
            for loc in updates.get("new_locations", []):
                if loc not in current_state["locations"]:
                    current_state["locations"].append(loc)
            
            # ── 章节摘要 ──
            if "chapters_summary" not in current_state:
                current_state["chapters_summary"] = {}
            current_state["chapters_summary"][str(chapter_num)] = updates.get("summary", "")
            
            # ── 时间线 ──
            timeline = updates.get("timeline", {})
            if timeline:
                if "timeline" not in current_state:
                    current_state["timeline"] = {"total_days": 0, "chapters": {}}
                prev_days = current_state["timeline"].get("total_days", 0)
                days = timeline.get("days_elapsed", 0)
                current_state["timeline"]["total_days"] = prev_days + days
                current_state["timeline"]["chapters"][str(chapter_num)] = timeline
            
            # ── 自动标记角色类型 ──
            self._auto_tag_roles(current_state["characters"])
            
            log.info(f"ContextUpdater v3: {len(new_chars)} chars updated for Ch{chapter_num}")
            return current_state
            
        except Exception as e:
            log.error(f"ContextUpdater v3 failed: {e}")
            return current_state

    def get_context_for_writer(self, novel_id: str, chapter_num: int, memory) -> str:
        """v3: 写作前注入角色当前状态到上下文
        
        这是关键方法——必须在 build_writer_context 中调用，
        让写手在动笔前就知道每个角色当前在哪、情绪如何、在做什么。
        """
        state_path = os.path.join(memory.get_novel_dir(novel_id), "global_state.json")
        if not os.path.exists(state_path):
            return ""
        
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
        except (IOError, UnicodeDecodeError, json.JSONDecodeError) as e:
            log.warning(f"Failed to read global_state for writer context: {e}")
            return ""
        
        parts = []
        
        # ── 角色当前状态表（最重要的部分）──
        chars = state.get("characters", {})
        if chars:
            parts.append("## 📋 角色当前状态（写作前必须检查！）\n")
            parts.append("> ⚠️ 以下状态是第{last_ch}章结束时的精确快照。写本章时角色行为必须与此一致。\n".format(
                last_ch=chapter_num - 1))
            parts.append("| 角色 | 位置 | 情绪 | 身体状况 | 当前目标 |")
            parts.append("|------|------|------|----------|----------|")
            
            # 主角优先，然后按最后出现章节排序
            sorted_chars = sorted(
                chars.items(),
                key=lambda x: (
                    0 if (isinstance(x[1], dict) and x[1].get("role") == "protagonist") else 1,
                    -(isinstance(x[1], dict) and x[1].get("last_seen_chapter", 0) or 0)
                )
            )
            
            for name, data in sorted_chars[:15]:
                if not isinstance(data, dict):
                    continue
                # 跳过死亡角色
                if data.get("health", "") == "已死亡":
                    continue
                loc = str(data.get("location", "?"))[:20]
                emo = str(data.get("emotion", "?"))[:10]
                hp = str(data.get("health", "?"))[:15]
                goal = str(data.get("current_goal", "?"))[:25]
                parts.append(f"| {name[:8]} | {loc} | {emo} | {hp} | {goal} |")
            
            # 死亡角色清单
            dead = [n for n, d in chars.items() if isinstance(d, dict) and d.get("health") == "已死亡"]
            if dead:
                parts.append(f"\n💀 已死亡: {', '.join(dead)}")
        
        # ── 时空位置 ──
        timeline = state.get("timeline", {})
        if timeline:
            days = timeline.get("total_days", 0)
            parts.append(f"\n### ⏱ 时间线\n- 故事已进行约 {days} 天")
            last_tl = timeline.get("chapters", {}).get(str(chapter_num - 1), {})
            if last_tl:
                parts.append(f"- 上一章发生在: {last_tl.get('chapter_start_time', '未知')}")
        
        # ── 最近剧情摘要 ──
        summaries = state.get("chapters_summary", {})
        if summaries:
            recent = sorted(
                [int(k) for k in summaries.keys() if int(k) >= max(1, chapter_num - 3) and int(k) < chapter_num]
            )
            if recent:
                parts.append("\n### 📖 近3章剧情")
                for ch in recent:
                    parts.append(f"- 第{ch}章: {summaries.get(str(ch), '?')}")
        
        # ── 已知地点 ──
        locations = state.get("locations", [])
        if locations:
            parts.append(f"\n### 🗺 已知地点\n{', '.join(locations[-10:])}")
        
        return "\n".join(parts)
    
    def _auto_tag_roles(self, characters: dict):
        """自动识别主角: 出现章节最多的角色"""
        if not characters:
            return
        best_name, best_count = None, 0
        for name, data in characters.items():
            if isinstance(data, dict):
                lsc = data.get("last_seen_chapter", 0)
                if lsc > best_count:
                    best_count = lsc
                    best_name = name
        if best_name and best_count >= 2:
            characters[best_name]["role"] = "protagonist"

    def _merge_active_characters(self, current_state: dict):
        """将 active_characters/protagonist_state 中的预置角色信息合并到 characters"""
        chars = current_state.get("characters", {})
        
        # 从 plan 的 protagonist_state 获取主角名
        protag = current_state.get("protagonist_state", {})
        if isinstance(protag, dict) and protag.get("name"):
            pname = protag["name"]
            if pname not in chars:
                chars[pname] = {"location": "未知", "emotion": "未知", "health": "未知",
                               "current_goal": "未知", "equipment": [], "last_seen_chapter": 0,
                               "relationships": {}}
            chars[pname]["role"] = "protagonist"
            chars[pname].setdefault("identity", protag.get("identity", ""))
        
        # 从 plan 的 active_characters 获取配角信息
        active = current_state.get("active_characters", {})
        if isinstance(active, dict):
            for name, data in active.items():
                if not isinstance(data, dict):
                    continue
                if name not in chars:
                    chars[name] = {"location": "未知", "emotion": "未知", "health": "未知",
                                  "current_goal": "未知", "equipment": [], "last_seen_chapter": 0,
                                  "relationships": {}}
                # 继承预设的身份和关系
                if "identity" in data:
                    chars[name]["identity"] = data["identity"]
                if "location" in data and data["location"] != "待定":
                    chars[name]["location"] = data["location"]
                if "status" in data:
                    chars[name]["health"] = data["status"]
                if "last_appeared" in data and data["last_appeared"] > 0:
                    chars[name]["last_seen_chapter"] = data["last_appeared"]
        
        current_state["characters"] = chars

    # ── 向后兼容 ──
    def get_context_for_chapter(self, novel_id: str, chapter_num: int, memory) -> str:
        return self.get_context_for_writer(novel_id, chapter_num, memory)
