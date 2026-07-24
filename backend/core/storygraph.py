"""NovelGenerator — StoryGraph: 剧情图谱系统

结构化记忆替代线性压缩，追踪四大维度：
1. plot_threads — 剧情线（主线/支线/角色弧）
2. foreshadow_ledger — 伏笔账本（全生命周期）
3. char_snapshots — 角色快照（实时状态防OOC）
4. causal_links — 因果链（A→B→C结构化关联）

每写完一章自动提取更新，writer 上下文按优先级加权注入。
"""
import json
import logging
import copy
from typing import Optional, Any

log = logging.getLogger(__name__)

# ── 数据结构 ──

DEFAULT_GRAPH = {
    "plot_threads": {},         # id -> Thread
    "foreshadow_ledger": {},    # id -> Foreshadow
    "char_snapshots": {},       # name -> CharSnapshot
    "causal_links": [],         # list of CausalLink
    "version": 0,
    "last_updated_chapter": 0,
}


class Thread:
    """一条剧情线"""
    __slots__ = ("id", "name", "type", "status", "priority", "description",
                 "key_nodes", "next_planned", "current_tension", "characters")
    
    def __init__(self, id: str, name: str, type: str = "main_plot",
                 status: str = "active", priority: int = 3,
                 description: str = ""):
        self.id = id
        self.name = name
        self.type = type          # main_plot | subplot | character_arc | mystery
        self.status = status      # dormant | active | advancing | climax | resolved
        self.priority = priority  # 1-5, 5最高
        self.description = description
        self.key_nodes = []       # [{"chapter": int, "event": str, "tension": int}]
        self.next_planned = ""
        self.current_tension = 5  # 1-10
        self.characters = []      # 关联角色名列表


class Foreshadow:
    """一条伏笔"""
    __slots__ = ("id", "description", "planted_chapter", "planned_payoff_chapter",
                 "actual_payoff_chapter", "status", "hint_count", "last_hint_chapter",
                 "thread_id", "importance")
    
    def __init__(self, id: str, description: str, planted_chapter: int,
                 planned_payoff_chapter: int, importance: int = 3):
        self.id = id
        self.description = description
        self.planted_chapter = planted_chapter
        self.planned_payoff_chapter = planned_payoff_chapter
        self.actual_payoff_chapter = None
        self.status = "planted"   # planted | hinted | revealed | resolved
        self.hint_count = 1
        self.last_hint_chapter = planted_chapter
        self.thread_id = ""       # 关联剧情线ID
        self.importance = importance  # 1-5


class CharSnapshot:
    """角色实时状态快照"""
    __slots__ = ("name", "last_chapter_appeared", "current_location",
                 "current_power_level", "status_effects", "known_secrets",
                 "relationship_changes", "current_emotion", "active_goals")
    
    def __init__(self, name: str):
        self.name = name
        self.last_chapter_appeared = 0
        self.current_location = ""
        self.current_power_level = ""
        self.status_effects = []      # ["左臂旧伤未愈", ...]
        self.known_secrets = []       # ["师父死因真相", ...]
        self.relationship_changes = []  # [{"with": "柳师妹", "change": "", "chapter": 0}]
        self.current_emotion = ""
        self.active_goals = []        # ["寻找神器", ...]


class CausalLink:
    """一条因果链"""
    __slots__ = ("cause_chapter", "cause_event", "effect_chapter",
                 "effect_event", "description", "status")
    
    def __init__(self, cause_chapter: int, cause_event: str,
                 effect_chapter: int, effect_event: str):
        self.cause_chapter = cause_chapter
        self.cause_event = cause_event
        self.effect_chapter = effect_chapter
        self.effect_event = effect_event
        self.description = f"第{cause_chapter}章「{cause_event}」→ 第{effect_chapter}章「{effect_event}」"
        self.status = "pending"  # pending | active | resolved


# ── StoryGraph 管理器 ──

class StoryGraph:
    """剧情图谱管理器 — 不依赖 SharedMemoryManager，直接操作 JSON 文件"""

    def __init__(self, graph_data: Optional[dict] = None):
        if graph_data:
            self.data = graph_data
            # 确保所有字段存在
            for k, v in DEFAULT_GRAPH.items():
                if k not in self.data:
                    self.data[k] = copy.deepcopy(v)
        else:
            self.data = copy.deepcopy(DEFAULT_GRAPH)

    # ── 剧情线操作 ──

    def get_active_threads(self, limit: int = 5) -> list:
        """获取当前活跃剧情线，按优先级排序"""
        threads = [t for t in self.data["plot_threads"].values()
                   if t["status"] in ("active", "advancing", "climax")]
        threads.sort(key=lambda t: (t["priority"], t["current_tension"]), reverse=True)
        return threads[:limit]

    def add_thread(self, thread_id: str, name: str, type: str = "subplot",
                   priority: int = 3, description: str = "") -> str:
        """新增剧情线"""
        self.data["plot_threads"][thread_id] = {
            "id": thread_id, "name": name, "type": type,
            "status": "active", "priority": priority,
            "description": description,
            "key_nodes": [], "next_planned": "",
            "current_tension": 5, "characters": [],
        }
        return thread_id

    def add_thread_node(self, thread_id: str, chapter: int, event: str, tension: int = None):
        """为剧情线添加关键节点"""
        thread = self.data["plot_threads"].get(thread_id)
        if not thread:
            return
        thread["key_nodes"].append({
            "chapter": chapter, "event": event,
            "tension": tension or thread.get("current_tension", 5),
        })
        thread["current_tension"] = tension or thread["current_tension"]

    def advance_thread(self, thread_id: str, new_status: str = None):
        """推进剧情线状态"""
        thread = self.data["plot_threads"].get(thread_id)
        if thread and new_status:
            thread["status"] = new_status

    def get_thread_summaries(self, thread_ids: list = None) -> str:
        """获取剧情线摘要文本（注入 writer 上下文用）"""
        if thread_ids:
            threads = [self.data["plot_threads"][tid] for tid in thread_ids
                       if tid in self.data["plot_threads"]]
        else:
            threads = list(self.data["plot_threads"].values())

        if not threads:
            return ""

        parts = ["## 📊 活跃剧情线追踪\n"]
        for t in sorted(threads, key=lambda x: x["priority"], reverse=True):
            status_icon = {"dormant": "💤", "active": "▶️", "advancing": "⚡",
                           "climax": "🔥", "resolved": "✅"}.get(t["status"], "❓")
            parts.append(
                f"{status_icon} {t['name']} [P{t['priority']}] "
                f"(紧张度: {t['current_tension']}/10)\n"
                f"  类型: {t['type']} | 状态: {t['status']}\n"
                f"  进度: "
            )
            nodes = t.get("key_nodes", [])
            if nodes:
                recent = nodes[-3:]
                parts.append(" → ".join(f"Ch{n['chapter']}:{n['event'][:15]}" for n in recent))
            else:
                parts.append("尚未有进展")
            if t.get("next_planned"):
                parts.append(f"\n  下一步: {t['next_planned']}")
            parts.append("\n")
        return "\n".join(parts)

    # ── 伏笔操作 ──

    def add_foreshadow(self, fs_id: str, description: str, planted_chapter: int,
                       planned_payoff: int, importance: int = 3,
                       thread_id: str = "") -> str:
        """添加伏笔"""
        self.data["foreshadow_ledger"][fs_id] = {
            "id": fs_id, "description": description,
            "planted_chapter": planted_chapter,
            "planned_payoff_chapter": planned_payoff,
            "actual_payoff_chapter": None,
            "status": "planted",
            "hint_count": 1, "last_hint_chapter": planted_chapter,
            "thread_id": thread_id, "importance": importance,
        }
        return fs_id

    def hint_foreshadow(self, fs_id: str, chapter: int):
        """标记伏笔被再次暗示"""
        fs = self.data["foreshadow_ledger"].get(fs_id)
        if fs:
            fs["hint_count"] += 1
            fs["last_hint_chapter"] = chapter
            if fs["status"] == "planted":
                fs["status"] = "hinted"

    def reveal_foreshadow(self, fs_id: str, chapter: int, resolved: bool = False):
        """标记伏笔被揭示/解决"""
        fs = self.data["foreshadow_ledger"].get(fs_id)
        if fs:
            fs["status"] = "resolved" if resolved else "revealed"
            fs["actual_payoff_chapter"] = chapter

    def get_due_foreshadows(self, current_chapter: int, window: int = 5) -> list:
        """获取即将到期的伏笔"""
        due = []
        for fs in self.data["foreshadow_ledger"].values():
            if fs["status"] in ("planted", "hinted"):
                remaining = fs["planned_payoff_chapter"] - current_chapter
                if 0 <= remaining <= window:
                    due.append((remaining, fs))
        due.sort(key=lambda x: (x[0], -x[1]["importance"]))
        return [d[1] for d in due]

    def get_overdue_foreshadows(self, current_chapter: int) -> list:
        """获取已过期的伏笔"""
        overdue = []
        for fs in self.data["foreshadow_ledger"].values():
            if fs["status"] in ("planted", "hinted"):
                if fs["planned_payoff_chapter"] < current_chapter:
                    overdue.append(fs)
        overdue.sort(key=lambda x: -x["importance"])
        return overdue

    def get_foreshadow_context(self, current_chapter: int) -> str:
        """构建伏笔上下文（注入 writer 用）"""
        due = self.get_due_foreshadows(current_chapter, window=3)
        overdue = self.get_overdue_foreshadows(current_chapter)

        parts = []
        if due:
            parts.append("## 📌 即将到期的伏笔\n")
            for fs in due:
                remaining = fs["planned_payoff_chapter"] - current_chapter
                urgency = "🔴 必须" if remaining <= 0 else ("🟡 建议" if remaining <= 1 else "🟢 可选")
                parts.append(
                    f"- {urgency} [Ch{fs['planted_chapter']}埋 → "
                    f"计划Ch{fs['planned_payoff_chapter']}回收] "
                    f"('{fs['description'][:40]}')\n"
                )

        if overdue:
            parts.append("\n## ⚠️ 已过期待回收的伏笔\n")
            for fs in overdue[:3]:
                parts.append(
                    f"- [计划Ch{fs['planned_payoff_chapter']}回收，已超Ch{current_chapter}] "
                    f"('{fs['description'][:40]}')\n"
                )

        return "\n".join(parts)

    # ── 角色快照操作 ──

    def ensure_char(self, name: str) -> dict:
        """确保角色存在并返回"""
        if name not in self.data["char_snapshots"]:
            self.data["char_snapshots"][name] = {
                "name": name, "last_chapter_appeared": 0,
                "current_location": "", "current_power_level": "",
                "status_effects": [], "known_secrets": [],
                "relationship_changes": [],
                "current_emotion": "", "active_goals": [],
            }
        return self.data["char_snapshots"][name]

    def update_char(self, name: str, chapter: int, **kwargs):
        """更新角色快照"""
        char = self.ensure_char(name)
        char["last_chapter_appeared"] = chapter
        for k, v in kwargs.items():
            if k in char and v is not None:
                char[k] = v

    def get_char_snapshots_text(self, char_names: list = None) -> str:
        """获取角色快照文本（注入 writer 用）"""
        snaps = self.data["char_snapshots"]
        if char_names:
            targets = {n: snaps[n] for n in char_names if n in snaps}
        else:
            targets = snaps

        if not targets:
            return ""

        parts = ["## 👤 角色当前状态\n"]
        for name, snap in targets.items():
            parts.append(f"**{name}**")
            if snap["current_location"]:
                parts.append(f"  位置: {snap['current_location']}")
            if snap["current_power_level"]:
                parts.append(f"  实力: {snap['current_power_level']}")
            if snap["current_emotion"]:
                parts.append(f"  情绪: {snap['current_emotion']}")
            if snap["status_effects"]:
                parts.append(f"  状态: {', '.join(snap['status_effects'])}")
            if snap["active_goals"]:
                parts.append(f"  目标: {', '.join(snap['active_goals'])}")
            parts.append("")
        return "\n".join(parts)

    # ── 因果链操作 ──

    def add_causal_link(self, cause_chapter: int, cause_event: str,
                        effect_chapter: int, effect_event: str):
        """添加因果链"""
        self.data["causal_links"].append({
            "cause_chapter": cause_chapter, "cause_event": cause_event,
            "effect_chapter": effect_chapter, "effect_event": effect_event,
            "description": f"Ch{cause_chapter}「{cause_event}」→ Ch{effect_chapter}「{effect_event}」",
            "status": "active",
        })

    # ── 序列化 ──

    def to_dict(self) -> dict:
        self.data["version"] += 1
        return self.data

    @classmethod
    def from_dict(cls, data: dict) -> "StoryGraph":
        return cls(data)


# ── AI 自动提取（轻量，每章后调用） ──

STORYGRAPH_EXTRACT_SYSTEM = """你是一个小说剧情分析助手。给定一章正文内容和当前剧情图谱，你需要：

1. 识别本章推进了哪些剧情线，及每条线的新进展
2. 识别新埋伏笔或已回收的伏笔
3. 识别角色状态变化（位置、伤势、情绪、关系等）
4. 识别因果事件（本章的结果是由前面哪章的事件导致的）

以 JSON 格式输出更新指令。只输出 JSON，不要其他内容。

JSON 格式：
{
  "thread_updates": [
    {"id": "thread_xx", "action": "advance|new|resolve|no_change",
     "name": "剧情线名", "type": "main_plot|subplot|character_arc|mystery",
     "priority": 3, "event": "关键事件描述", "tension": 7}
  ],
  "foreshadow_updates": [
    {"id": "fs_xx", "action": "plant|hint|reveal|resolve",
     "description": "伏笔描述",
     "planned_payoff": 45,
     "importance": 3, "thread_id": "thread_xx"}
  ],
  "char_updates": [
    {"name": "角色名",
     "location": "当前位置或null",
     "power_level": "当前实力或null",
     "emotion": "当前情绪或null",
     "status_effects": ["列表或空数组"],
     "secrets_learned": ["新知道的秘密或空"],
     "goals": ["当前目标或空"],
     "relationship_changes": [{"with": "对方名", "change": "关系变化描述"}]
  ],
  "causal_links": [
    {"cause_chapter": 12, "cause_event": "起因事件",
     "effect_chapter": 15, "effect_event": "结果事件"}
  ],
  "thread_analysis": "一段话分析本章在剧情结构中的作用（如'本为主线推进章，为下章高潮做铺垫'）"
}"""


def extract_storygraph_from_chapter(
    chapter_text: str, current_graph: dict, chapter_num: int,
    chapter_outline: dict, client, model: str = None
) -> dict:
    """用 LLM 从章节正文中提取剧情更新

    Args:
        chapter_text: 本章正文（取前3000字+后2000字，省token）
        current_graph: 当前剧情图谱数据
        chapter_num: 本章号
        chapter_outline: 本章大纲
        client: OpenAI-compatible client
        model: 用便宜模型，默认None=用主模型
    """
    # 截取正文（取中间的核心内容）
    text = chapter_text
    if len(text) > 5000:
        text = text[:3000] + "\n...[中略]...\n" + text[-2000:]

    # 构建当前剧情图谱摘要
    graph_summary = _summarize_graph(current_graph)

    prompt = f"""## 当前剧情图谱

{graph_summary}

## 本章信息

- 章节: 第{chapter_num}章
- 标题: {chapter_outline.get('title', '')}
- 核心事件: {chapter_outline.get('summary', '')}
- 出场角色: {', '.join(chapter_outline.get('characters', []))}

## 本章正文

{text}"""

    try:
        resp = client.chat.completions.create(
            model=model or client.model,
            messages=[
                {"role": "system", "content": STORYGRAPH_EXTRACT_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=2000,
            response_format={"type": "json_object"},
        )
        result = json.loads(resp.choices[0].message.content)
        log.info(f"StoryGraph extract: {len(result.get('thread_updates', []))} threads, "
                 f"{len(result.get('foreshadow_updates', []))} foreshadows, "
                 f"{len(result.get('char_updates', []))} characters")
        return result
    except Exception as e:
        log.warning(f"StoryGraph extraction failed: {e}")
        return {"thread_updates": [], "foreshadow_updates": [],
                "char_updates": [], "causal_links": [], "thread_analysis": ""}


def apply_extraction(graph: StoryGraph, result: dict, chapter_num: int):
    """将提取结果应用到剧情图谱"""
    # 剧情线更新
    for tu in result.get("thread_updates", []):
        tid = tu.get("id", "")
        action = tu.get("action", "no_change")
        if action == "new" and tid:
            graph.add_thread(tid, tu.get("name", ""), tu.get("type", "subplot"),
                             tu.get("priority", 3))
        if tid in graph.data["plot_threads"]:
            if action == "resolve":
                graph.advance_thread(tid, "resolved")
            if tu.get("event"):
                graph.add_thread_node(tid, chapter_num, tu["event"], tu.get("tension"))

    # 伏笔更新
    for fu in result.get("foreshadow_updates", []):
        fid = fu.get("id", "")
        action = fu.get("action", "")
        if action == "plant" and fid:
            graph.add_foreshadow(fid, fu.get("description", ""), chapter_num,
                                 fu.get("planned_payoff", chapter_num + 20),
                                 fu.get("importance", 3), fu.get("thread_id", ""))
        elif action == "hint" and fid:
            graph.hint_foreshadow(fid, chapter_num)
        elif action == "reveal" and fid:
            graph.reveal_foreshadow(fid, chapter_num, resolved=False)
        elif action == "resolve" and fid:
            graph.reveal_foreshadow(fid, chapter_num, resolved=True)

    # 角色更新
    for cu in result.get("char_updates", []):
        name = cu.get("name", "")
        if not name:
            continue
        graph.update_char(name, chapter_num,
                          current_location=cu.get("location"),
                          current_power_level=cu.get("power_level"),
                          current_emotion=cu.get("emotion"),
                          status_effects=cu.get("status_effects"),
                          active_goals=cu.get("goals"))
        # 关系变化
        for rc in cu.get("relationship_changes", []):
            graph.ensure_char(name)
            graph.data["char_snapshots"][name]["relationship_changes"].append({
                "with": rc.get("with", ""), "change": rc.get("change", ""),
                "chapter": chapter_num,
            })
        # 新秘密
        for sec in cu.get("secrets_learned", []):
            if sec not in graph.data["char_snapshots"][name]["known_secrets"]:
                graph.data["char_snapshots"][name]["known_secrets"].append(sec)

    # 因果链
    for cl in result.get("causal_links", []):
        graph.add_causal_link(cl.get("cause_chapter", 0), cl.get("cause_event", ""),
                              chapter_num, cl.get("effect_event", ""))

    graph.data["last_updated_chapter"] = chapter_num


def _summarize_graph(data: dict) -> str:
    """生成剧情图谱的摘要文本（给LLM提取用）"""
    parts = []

    threads = data.get("plot_threads", {})
    if threads:
        parts.append("### 当前剧情线")
        for t in sorted(threads.values(), key=lambda x: x.get("priority", 0), reverse=True)[:8]:
            nodes = t.get("key_nodes", [])
            last_event = f"最后: Ch{nodes[-1]['chapter']}" if nodes else "新线"
            parts.append(f"- [{t.get('status','?')}] {t.get('name','?')} (P{t.get('priority',1)}) {last_event}")

    fs = data.get("foreshadow_ledger", {})
    unresolved = [f for f in fs.values() if f.get("status") in ("planted", "hinted")]
    if unresolved:
        parts.append(f"\n### 未回收伏笔 ({len(unresolved)}条)")
        for f in unresolved[:5]:
            parts.append(f"- Ch{f.get('planted_chapter','?')}→计划Ch{f.get('planned_payoff_chapter','?')}: {f.get('description','')[:30]}")

    chars = data.get("char_snapshots", {})
    if chars:
        parts.append(f"\n### 角色 ({len(chars)}人)")
        for name, snap in list(chars.items())[:5]:
            loc = snap.get("current_location", "") or "未知"
            parts.append(f"- {name}: 位于{loc}")

    return "\n".join(parts)
