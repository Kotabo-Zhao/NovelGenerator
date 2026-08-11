"""Priority-aware prompt context budgeting."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class ContextSection:
    name: str
    text: str
    priority: int
    required: bool = False


def _trim(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 80:
        return text[:limit]
    head = max(40, int(limit * 0.65))
    tail = limit - head
    return text[:head] + "\n…[context trimmed]…\n" + text[-tail:]


def allocate_context(sections: Iterable[ContextSection], max_chars: int) -> tuple[str, list[str]]:
    """Keep high-priority sections before spending budget on background text.

    Returns the rendered context and section names that were trimmed or dropped.
    Required sections are always represented, but are still bounded by the
    total budget so an oversized user input cannot exhaust the prompt.
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

    remaining = max_chars
    selected: dict[str, str] = {}
    trimmed: list[str] = []
    for section in sorted(
        normalized,
        key=lambda item: (-int(item.required), -item.priority, normalized.index(item)),
    ):
        if remaining <= 0:
            trimmed.append(section.name)
            continue
        allowance = min(len(section.text), remaining)
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
