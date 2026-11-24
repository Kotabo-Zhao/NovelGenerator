"""Dynamic Narrative Context Compiler with CJK semantic-boundary budgeting.

Compiles a lean, high-signal prompt context directly from the SQLite
NarrativeLedgerDB: active chapter contract, previous chapter bridge/anchor,
only the characters appearing in this chapter (latest state + voice notes),
due foreshadows, recent canon events, and FTS5-retrieved world entries.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .database import NarrativeLedgerDB
from .models import ChapterContract

_TRIM_MARKER = "\n…[已按语义边界压缩]…\n"
_SENTENCE_BREAKS = ("。", "！", "？", "；", "\n", ".", "!", "?")


@dataclass(frozen=True)
class ContextBlock:
    name: str
    text: str
    priority: int
    required: bool = False


def _snap_head(text: str, limit: int) -> int:
    if limit >= len(text):
        return len(text)
    floor = max(1, int(limit * 0.65))
    window = text[floor:limit]
    para = window.rfind("\n\n")
    if para != -1:
        return floor + para
    best = max((window.rfind(sep) for sep in _SENTENCE_BREAKS), default=-1)
    return floor + best + 1 if best != -1 else limit


def _snap_tail(text: str, tail_len: int) -> int:
    start = max(0, len(text) - tail_len)
    if start == 0:
        return 0
    ceiling = min(len(text), start + max(1, int(tail_len * 0.35)))
    window = text[start:ceiling]
    para = window.find("\n\n")
    if para != -1:
        return start + para + 2
    for sep in _SENTENCE_BREAKS:
        idx = window.find(sep)
        if idx != -1:
            return start + idx + 1
    return start


def trim_semantic(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 80:
        return text[:limit]
    usable = max(40, limit - len(_TRIM_MARKER))
    head_budget = max(30, int(usable * 0.65))
    tail_budget = max(10, usable - head_budget)
    head_end = _snap_head(text, head_budget)
    tail_start = _snap_tail(text, tail_budget)
    if tail_start <= head_end:
        return text[:limit]
    return text[:head_end].rstrip() + _TRIM_MARKER + text[tail_start:].lstrip()


def compile_budgeted_blocks(blocks: Iterable[ContextBlock], max_chars: int = 7500) -> tuple[str, list[str]]:
    normalized = [b for b in blocks if b.text and b.text.strip()]
    if not normalized:
        return "", []
    ordered = sorted(normalized, key=lambda b: (-int(b.required), -b.priority, normalized.index(b)))
    req_items = [b for b in ordered if b.required]
    req_floors: dict[str, int] = {}
    if req_items:
        per_floor = max(100, max_chars // max(len(req_items) + 1, 2))
        for item in req_items:
            req_floors[item.name] = min(len(item.text), per_floor)

    remaining = max_chars
    reserved = sum(req_floors.values())
    chosen: dict[str, str] = {}
    trimmed: list[str] = []

    for block in ordered:
        if block.required:
            my_floor = req_floors.get(block.name, 0)
            reserved = max(0, reserved - my_floor)
            avail = max(my_floor, remaining - reserved)
        else:
            avail = max(0, remaining - reserved)
        allowance = min(len(block.text), avail, remaining)
        if allowance <= 0:
            trimmed.append(block.name)
            continue
        chosen[block.name] = trim_semantic(block.text, allowance)
        remaining -= allowance
        if allowance < len(block.text):
            trimmed.append(block.name)

    parts = [f"### {b.name}\n{chosen[b.name]}" for b in normalized if b.name in chosen]
    return "\n\n".join(parts), trimmed


class NarrativeContextCompiler:
    """Builds focused, state-consistent prompts from NarrativeLedgerDB."""

    def __init__(self, db: NarrativeLedgerDB):
        self.db = db

    def compile_for_chapter(
        self,
        novel: dict[str, Any],
        contract: ChapterContract,
        *,
        revision_feedback: str = "",
        max_chars: int = 7500,
    ) -> dict[str, Any]:
        novel_id = novel["id"]
        ch_num = contract.chapter_num
        ledger = self.db.get_ledger(novel_id)

        # 1. Chapter Contract (highest priority, required)
        contract_lines = [
            f"章节：第{ch_num}章《{contract.title}》",
            f"叙事视角：{contract.pov or novel.get('pov', '第三人称限知')}",
            f"目标字数：约 {contract.target_words} 字",
        ]
        if contract.summary:
            contract_lines.append(f"场景概要：{contract.summary}")
        if contract.goal:
            contract_lines.append(f"主角本章目标：{contract.goal}")
        if contract.conflict:
            contract_lines.append(f"核心冲突：{contract.conflict}")
        if contract.must_happen:
            contract_lines.append("必须落地的事件：\n" + "\n".join(f"- {e}" for e in contract.must_happen))
        if contract.hook_direction:
            contract_lines.append(f"章末收束与钩子方向：{contract.hook_direction}")

        # 2. Previous chapter bridge & ending anchor (required when ch > 1)
        bridge_text = ""
        if ch_num > 1:
            prev = self.db.get_chapter(novel_id, ch_num - 1)
            if prev:
                b_parts = []
                if prev.get("bridge_summary"):
                    b_parts.append(f"上章剧情承接：{prev['bridge_summary']}")
                if prev.get("ending_anchor"):
                    b_parts.append(f"上章结尾原文锚点：\n{prev['ending_anchor']}")
                elif prev.get("content"):
                    b_parts.append(f"上章结尾原文锚点：\n{prev['content'][-180:]}")
                bridge_text = "\n".join(b_parts)

        # 3. Active characters & entities (prioritize those named in contract)
        target_chars = set(contract.characters)
        entity_lines = []
        for ent in ledger["entities"]:
            is_featured = ent["name"] in target_chars or not target_chars
            if is_featured:
                line = f"- {ent['name']}（{ent['kind']}）"
                if ent["location"]:
                    line += f"｜当前位置：{ent['location']}"
                if ent["current_state"]:
                    line += f"｜当前状态：{ent['current_state']}"
                if ent["profile"]:
                    line += f"｜设定：{ent['profile']}"
                if ent["voice_notes"]:
                    line += f"｜说话风格：{ent['voice_notes']}"
                entity_lines.append(line)

        # 4. Due or active foreshadows
        fs_lines = []
        for fs in ledger["foreshadows"]:
            if fs["status"] != "resolved" and fs["planted_chapter"] <= ch_num:
                urgency = "【本章应推进/回收】" if fs["target_chapter"] <= ch_num else f"（预计第{fs['target_chapter']}章回收）"
                fs_lines.append(f"- {fs['title']} {urgency}：{fs['detail']}")

        # 5. Canon events (strict anti-repetition constraint)
        canon_lines = [
            f"- 第{ev['chapter_num']}章：{ev['summary']}"
            for ev in ledger["canon_events"]
            if ev["chapter_num"] < ch_num
        ][-15:]

        # 6. FTS5 active retrieval for world rules & past context relevant to this chapter
        query_seed = " ".join([contract.title, contract.summary, contract.conflict, " ".join(contract.characters)])
        fts_hits = self.db.search_ledger(novel_id, query_seed, limit=6)
        world_lines = []
        for hit in fts_hits:
            if hit["doc_type"] in ("world", "foreshadow"):
                world_lines.append(f"- [{hit['doc_type']}] {hit['title']}：{hit['snippet']}")
        if not world_lines and ledger["world_entries"]:
            for w in ledger["world_entries"][:5]:
                world_lines.append(f"- {w['title']}：{w['content'][:160]}")

        blocks = [
            ContextBlock("本章写作契约（硬约束）", "\n".join(contract_lines), priority=100, required=True),
            ContextBlock("修订意见（最高优先）", revision_feedback.strip(), priority=98, required=bool(revision_feedback.strip())),
            ContextBlock("上章承接与结尾锚点", bridge_text, priority=92, required=bool(bridge_text)),
            ContextBlock("出场角色与当前状态账本", "\n".join(entity_lines), priority=85, required=bool(entity_lines)),
            ContextBlock("已发生正史事件（严禁复述重演）", "\n".join(canon_lines), priority=78, required=False),
            ContextBlock("待推进/回收伏笔", "\n".join(fs_lines), priority=70, required=False),
            ContextBlock("相关世界观与规则检索", "\n".join(world_lines), priority=60, required=False),
            ContextBlock("全书核心设定", f"《{novel['title']}》｜题材：{novel['genre']}｜风格：{novel['style']}\n{novel['premise']}", priority=50, required=False),
        ]
        compiled_text, trimmed = compile_budgeted_blocks(blocks, max_chars=max_chars)
        return {
            "compiled_context": compiled_text,
            "trimmed_sections": trimmed,
            "active_entities": len(entity_lines),
            "active_foreshadows": len(fs_lines),
            "canon_events_count": len(canon_lines),
        }
