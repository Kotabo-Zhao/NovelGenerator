"""角色记忆系统 v3.5.9 — 对话/行动真实沉淀为角色专属记忆

问题背景（老赵）："对话会真实作用到角色的记忆里吗？"
- 旧机制：PACT facts 是全局共享的（角色 A 知道的 B 也被迫知道）、
  只沉淀"承诺类"、对话历史 16 条就丢——没有"角色视角"的记忆。

本模块：
- state.memories[角色名] = [{ts, type, content, source}]  — 角色专属记忆库
  type: promise(承诺)/event(事件)/secret(秘密)/attitude(态度)/info(信息)
- state.events = [{ts, type, summary}]                    — 全局事件时间线（最近 6 条）
- 每个角色记忆上限 30 条，内容级去重，防膨胀
"""
from __future__ import annotations

import time
from typing import Optional

MEMORY_LIMIT = 30
EVENT_LIMIT = 8


def add_memory(state: dict, char: str, mtype: str, content: str,
               source: str = "", ts: Optional[str] = None) -> None:
    """给角色加一条记忆（内容级去重：前 30 字相同视为重复）"""
    if not char or not content:
        return
    mems = state.setdefault("memories", {}).setdefault(char, [])
    key = str(content)[:30]
    for m in mems[-12:]:
        if str(m.get("content", ""))[:30] == key:
            return
    mems.append({
        "ts": ts or time.strftime("%m-%d %H:%M"),
        "type": mtype,
        "content": str(content)[:120],
        "source": str(source)[:40],
    })
    if len(mems) > MEMORY_LIMIT:
        del mems[: len(mems) - MEMORY_LIMIT]


def add_event(state: dict, summary: str, etype: str = "event") -> None:
    """全局事件时间线（最近 N 条，供状态卡'刚发生的事'展示）"""
    if not summary:
        return
    evs = state.setdefault("events", [])
    evs.append({
        "ts": time.strftime("%H:%M"),
        "type": etype,
        "summary": str(summary)[:80],
    })
    if len(evs) > EVENT_LIMIT:
        del evs[: len(evs) - EVENT_LIMIT]


def get_memories(state: dict, char: str, n: int = 8) -> list:
    """角色记忆（最近 n 条，按时间倒序）"""
    mems = (state.get("memories") or {}).get(char, [])
    return mems[-n:][::-1]


def memory_brief(state: dict, char: str, n: int = 8) -> str:
    """角色记忆 → prompt 文本（'你记得的事'）"""
    mems = get_memories(state, char, n)
    if not mems:
        return ""
    lines = []
    for m in mems:
        tag = {"promise": "承诺", "event": "事件", "secret": "秘密",
               "attitude": "态度", "info": "见闻"}.get(m.get("type", "event"), "事件")
        lines.append(f"- [{tag}] {m.get('content', '')}")
    return "你记得的事（必须真实反映在态度和回应里，读者做过的事你不能装作不知道）:\n" + "\n".join(lines)


def events_brief(state: dict, n: int = 4) -> str:
    """事件时间线 → prompt/快照文本"""
    evs = (state.get("events") or [])[-n:]
    if not evs:
        return ""
    return "；".join(f"{e.get('ts', '')} {e.get('summary', '')}" for e in evs)
