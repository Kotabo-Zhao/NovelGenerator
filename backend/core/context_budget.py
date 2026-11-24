"""Priority-aware prompt context budgeting with CJK semantic boundary trimming."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class ContextSection:
    name: str
    text: str
    priority: int
    required: bool = False


_TRIM_MARKER = "\n…[context trimmed]…\n"
_SENTENCE_BREAKS = ("。", "！", "？", "；", "\n", ".", "!", "?")


def _snap_head_boundary(text: str, limit: int) -> int:
    """Pull head cut point back to the nearest paragraph or sentence boundary."""
    if limit >= len(text):
        return len(text)
    floor = max(1, int(limit * 0.65))
    window = text[floor:limit]
    para = window.rfind("\n\n")
    if para != -1:
        return floor + para
    best = -1
    for sep in _SENTENCE_BREAKS:
        idx = window.rfind(sep)
        if idx > best:
            best = idx
    if best != -1:
        return floor + best + 1
    return limit


def _snap_tail_boundary(text: str, tail_len: int) -> int:
    """Push tail start forward to the nearest paragraph or sentence boundary."""
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


def _trim(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 80:
        return text[:limit]
    usable = max(40, limit - len(_TRIM_MARKER))
    head_budget = max(30, int(usable * 0.65))
    tail_budget = max(10, usable - head_budget)
    head_end = _snap_head_boundary(text, head_budget)
    tail_start = _snap_tail_boundary(text, tail_budget)
    if tail_start <= head_end:
        return text[:limit]
    return text[:head_end].rstrip() + _TRIM_MARKER + text[tail_start:].lstrip()


def allocate_context(sections: Iterable[ContextSection], max_chars: int) -> tuple[str, list[str]]:
    """Keep high-priority sections before spending budget on background text.

    Returns the rendered context and section names that were trimmed or dropped.
    Required sections are guaranteed a fair-share floor so an oversized early
    section cannot starve later required sections, while still staying bounded
    by ``max_chars``.
    """

    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 200:
        raise ValueError("max_chars must be an integer >= 200")
    normalized = [
        ContextSection(s.name, s.text or "", int(s.priority), bool(s.required))
        for s in sections
        if isinstance(s, ContextSection) and s.text
    ]
    if not normalized:
        return "", []

    ordered = sorted(
        normalized,
        key=lambda item: (-int(item.required), -item.priority, normalized.index(item)),
    )

    # Reserve a fair-share floor for required sections so later required items
    # (e.g., current chapter outline or continuity bridge) are never starved.
    required_items = [s for s in ordered if s.required]
    required_floors: dict[str, int] = {}
    if required_items:
        per_req_floor = max(60, max_chars // max(len(required_items) + 1, 2))
        for req in required_items:
            required_floors[req.name] = min(len(req.text), per_req_floor)

    remaining = max_chars
    selected: dict[str, str] = {}
    trimmed: list[str] = []
    reserved_remaining = sum(required_floors.values())

    for section in ordered:
        if section.required:
            my_floor = required_floors.get(section.name, 0)
            reserved_remaining = max(0, reserved_remaining - my_floor)
            available = max(my_floor, remaining - reserved_remaining)
        else:
            available = max(0, remaining - reserved_remaining)

        if available <= 0:
            trimmed.append(section.name)
            continue

        allowance = min(len(section.text), available, remaining)
        if allowance <= 0:
            trimmed.append(section.name)
            continue

        selected[section.name] = _trim(section.text, allowance)
        remaining -= allowance
        if allowance < len(section.text):
            trimmed.append(section.name)

    rendered = []
    for section in normalized:
        value = selected.get(section.name)
        if value:
            rendered.append(f"## {section.name}\n{value}")
    return "\n\n".join(rendered), trimmed
