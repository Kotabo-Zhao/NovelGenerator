"""角色记忆系统 v3.5.9 — 对话/行动真实沉淀为角色专属记忆

问题背景（老赵）："对话会真实作用到角色的记忆里吗？"
- 旧机制：PACT facts 是全局共享的（角色 A 知道的 B 也被迫知道）、
  只沉淀"承诺类"、对话历史 16 条就丢——没有"角色视角"的记忆。

本模块：
- state.memories[角色名] = [{ts, type, content, source}]  — 角色专属记忆库
  type: promise(承诺)/event(事件)/secret(秘密)/attitude(态度)/info(信息)/summary(长期摘要)
- state.events = [{ts, type, summary}]                    — 全局事件时间线（最近 6 条）
- 每个角色记忆上限 30 条，内容级去重，防膨胀
- v3.5.31: 滚动压缩——满额时旧记忆经 LLM 提炼为"长期摘要"（summary 类），
  替代硬删除；摘要再满再合并（两级摘要）。后期剧情仍记得早期关键事件。
"""
from __future__ import annotations

import time
import re
from typing import Optional

MEMORY_LIMIT = 30
ARCHIVE_LIMIT = 200
EVENT_LIMIT = 30  # v2.5.60: 8→30 扩容——关键事件（摊牌/决裂/约定）不被挤出记忆窗口
SUMMARY_LIMIT = 6        # v3.5.31: 摘要条数上限（满后再合并最旧摘要）
COMPRESS_KEEP = 5        # v3.5.31: 压缩时保留最近 N 条原始记忆（保鲜窗口）
COMPRESS_BATCH = 12      # v3.5.31: 每次压缩处理最旧 N 条

# v3.5.31: 记忆压缩 prompt——把旧记忆提炼为长期摘要
MEMORY_COMPRESS_SYSTEM = """你是小说角色的记忆整理师。把角色的一批旧记忆压缩为 1-2 条长期摘要。

保留（必须）：
- 关键人物及其对读者的态度/关系变化
- 重大事件、重要承诺/威胁/秘密（含未兑现/未解开的）
- 影响角色立场的情感转折

丢弃：日常琐事、重复信息、已被后续记忆覆盖的细节

要求：
- 每条摘要 40-80 字，保留具体人名/事件名（不泛化为"某人""某事"）
- 最多 2 条，按时间顺序组织
- 只输出摘要文本行（"- 摘要"格式），不要解释"""


def add_memory(state: dict, char: str, mtype: str, content: str,
               source: str = "", ts: Optional[str] = None, event_id: str = "") -> None:
    """Record a character's evidence, retaining older entries for relevant recall."""
    if not char or not content:
        return
    mems = state.setdefault("memories", {}).setdefault(char, [])
    key = str(content)[:600]
    for m in mems[-12:]:
        if (event_id and m.get('event_id') == event_id) or str(m.get("content", "")) == key:
            return
    mems.append({
        "ts": ts or time.strftime("%m-%d %H:%M"),
        "type": mtype,
        "content": key,
        "source": str(source)[:40],
        "event_id": event_id,
    })
    if len(mems) > MEMORY_LIMIT:
        # v3.5.31: 标记待压缩（硬删除仅作兜底，实际压缩在章节结束时后台执行）
        dirty = state.setdefault("_memory_dirty", [])
        if char not in dirty:
            dirty.append(char)
        archive = state.setdefault('memory_archive', {}).setdefault(char, [])
        archive.extend(mems[:len(mems) - MEMORY_LIMIT])
        while len(archive) > ARCHIVE_LIMIT:
            index = next((i for i,m in enumerate(archive) if m.get('type') not in ('promise','secret','summary')), 0)
            archive.pop(index)
        del mems[: len(mems) - MEMORY_LIMIT]


def compress_char_memories(state: dict, char: str, llm_fn) -> bool:
    """v3.5.31: 压缩单个角色的旧记忆为长期摘要（LLM 提炼）

    触发：记忆接近上限且有可压缩的旧记忆。保留最近 COMPRESS_KEEP 条原始，
    最旧的 COMPRESS_BATCH 条 → 1-2 条 summary 摘要；摘要超上限再合并最旧摘要。
    """
    mems = state.get("memories", {}).get(char) or []
    if len(mems) < MEMORY_LIMIT - 4:
        return False  # 未接近上限，不压缩
    old = mems[:-COMPRESS_KEEP] if len(mems) > COMPRESS_KEEP else []
    if not old:
        return False
    batch = old[:COMPRESS_BATCH]
    if not batch:
        return False
    try:
        src_text = "\n".join(
            f"- [{m.get('type', 'event')}] {m.get('content', '')}" for m in batch)
        raw = llm_fn(MEMORY_COMPRESS_SYSTEM, f"压缩以下记忆：\n{src_text}",
                     temperature=0.3, max_tokens=600)
        if not raw or len(str(raw).strip()) < 20:
            return False
        summaries = [l.strip("- ").strip() for l in str(raw).splitlines()
                     if l.strip().startswith("-") and len(l.strip()) > 10][:2]
        if not summaries:
            return False
        # 移除被压缩的旧记忆
        remove_set = {id(m) for m in batch}
        mems[:] = [m for m in mems if id(m) not in remove_set]
        # 摘要写入（带 summary 标记）
        for s in summaries:
            mems.append({
                "ts": time.strftime("%m-%d %H:%M"),
                "type": "summary",
                "content": str(s)[:200],
                "source": "compress",
                "summary": True,
            })
        # 摘要超上限 → 合并最旧两条摘要
        sums = [m for m in mems if m.get("summary")]
        if len(sums) > SUMMARY_LIMIT:
            oldest = sums[:2]
            try:
                raw2 = llm_fn(MEMORY_COMPRESS_SYSTEM,
                              "压缩以下两条长期摘要为一条：\n" + "\n".join(
                                  f"- {m.get('content', '')}" for m in oldest),
                              temperature=0.3, max_tokens=400)
                merged = [l.strip("- ").strip() for l in str(raw2 or "").splitlines()
                          if l.strip().startswith("-") and len(l.strip()) > 10]
                if merged:
                    oids = {id(m) for m in oldest}
                    mems[:] = [m for m in mems if id(m) not in oids]
                    mems.append({"ts": time.strftime("%m-%d %H:%M"), "type": "summary",
                                 "content": str(merged[0])[:200], "source": "compress",
                                 "summary": True})
            except Exception:
                pass
        # 仍超上限则兜底截断
        if len(mems) > MEMORY_LIMIT + SUMMARY_LIMIT:
            del mems[: len(mems) - (MEMORY_LIMIT + SUMMARY_LIMIT)]
        dirty = state.get("_memory_dirty") or []
        if char in dirty:
            dirty.remove(char)
        return True
    except Exception:
        return False


def compress_all_memories(state: dict, llm_fn) -> int:
    """v3.5.31: 压缩所有需要压缩的角色记忆（章节结束时后台调用）"""
    if not llm_fn:
        return 0
    dirty = set(state.get("_memory_dirty") or [])
    chars = dirty or set((state.get("memories") or {}).keys())
    done = 0
    for ch in list(chars):
        if compress_char_memories(state, ch, llm_fn):
            done += 1
    return done


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


def get_memories(state: dict, char: str, n: int = 8, query: str = '') -> list:
    """角色记忆（最近 n 条，按时间倒序）"""
    mems = (state.get("memories") or {}).get(char, [])
    if n <= 0:
        return []
    if not query:
        return mems[-n:][::-1]
    entries = ((state.get('memory_archive') or {}).get(char) or []) + mems
    terms = set(re.findall(r'[a-zA-Z]{3,}', query.lower()))
    for run in re.findall(r'[\u4e00-\u9fff]+', query):
        terms.update(run[i:i+2] for i in range(len(run)-1))
    recent = list(range(max(0, len(entries)-min(2, n)), len(entries)))[::-1]
    ranked = sorted(range(len(entries)), key=lambda i: (
        sum(t in entries[i].get('content','').lower() for t in terms),
        entries[i].get('type') in ('promise','secret','summary'), i), reverse=True)
    chosen = (ranked[:max(0,n-len(recent))] + recent)
    chosen = list(dict.fromkeys(chosen))
    chosen += [i for i in ranked if i not in chosen][:n-len(chosen)]
    return [entries[i] for i in chosen[:n]]


def memory_brief(state: dict, char: str, n: int = 8, query: str = '') -> str:
    """角色记忆 → prompt 文本（'你记得的事'）"""
    mems = get_memories(state, char, n, query)
    if not mems:
        return ""
    lines = []
    for m in mems:
        if m.get("summary"):
            tag = "📌 长期记忆"
        else:
            tag = {"promise": "承诺", "event": "事件", "secret": "秘密",
                   "attitude": "态度", "info": "见闻"}.get(m.get("type", "event"), "事件")
        lines.append(f"- [{tag}] {m.get('content', '')}")
    return ("你记得的事（v3.5.40 记忆回响：这些必须真实反映在态度里；存在自然时机时"
            "可以主动提起——旧承诺、共同经历、读者做过的事，'你当初答应我的事'这类"
            "延迟回响比装作不记得更有戏；但不要每轮都提）:\n" + "\n".join(lines))


def events_brief(state: dict, n: int = 4) -> str:
    """事件时间线 → prompt/快照文本"""
    evs = (state.get("events") or [])[-n:]
    if not evs:
        return ""
    return "；".join(f"{e.get('ts', '')} {e.get('summary', '')}" for e in evs)


def remember_action(state: dict, result: dict, witnesses: list) -> None:
    """Only witnesses remember an executed attempt, including its actual outcome."""
    if result.get('status') not in ('success', 'failure'):
        return  # A hypothetical or refused intention is not an observed event.
    player = (state.get('player_char') or {}).get('name') or '玩家'
    outcome = '成功' if result['status'] == 'success' else '失败'
    content = (f"亲历：{player}尝试「{result.get('input','')[:180]}」；判定{outcome}。"
               f"{result.get('reason','')[:120]}；实际变化：{'；'.join(result.get('changes') or []) or '无'}")
    for name in dict.fromkeys(witnesses):
        add_memory(state, name, 'event', content, source='action', event_id=result['id'])
    add_event(state, f"{player}行动{outcome}：{result.get('input','')[:50]}", 'action')


def remember_dialogue(state: dict, target: str, user_text: str, reply: str, turn_id: str) -> None:
    """Remember direct testimony without promoting claims to world facts."""
    if target not in (state.get('casts') or {}):
        return
    player = (state.get('player_char') or {}).get('name') or '玩家'
    kind = 'promise' if re.search(r'答应|承诺|保证|约定', user_text) else 'info'
    content = f"交谈记录（说法不等于事实）：{player}对你说「{user_text[:260]}」；你回答「{reply[:240]}」"
    add_memory(state, target, kind, content, source='dialogue', event_id=turn_id)
    if state.get('participation') and kind == 'promise':
        from .participation import request_kind, _typed
        polarity, clause = request_kind(user_text)
        if polarity != 'affirmative':
            return
        typed = _typed(clause, state, {'recipient':target})
        anchor = re.search(r'明天|后天|今晚|明早|周[一二三四五六日天]|星期[一二三四五六日天]|[一二三四五六七八九十\d]+天后', user_text)
        ledger = state.setdefault('pending_promises', [])
        if any(p.get('who') == target and p.get('what') == user_text[:260] and p.get('status') == 'pending' for p in ledger):
            return
        ledger.append({'id':turn_id, 'who':target, 'what':user_text[:260],
                       'when_raw':anchor[0] if anchor else '', 'status':'pending',
                       'source':'direct_dialogue', 'fulfillment':typed,
                       'scene_num':state.get('scene_num',0)})
