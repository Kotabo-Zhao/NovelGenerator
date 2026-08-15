"""Deterministic pre-persistence checks for generated chapter text."""

from __future__ import annotations

import re


class ChapterQualityError(ValueError):
    pass


def validate_chapter_text(text: str, *, target_words: int = 1500) -> dict:
    if not isinstance(text, str):
        raise ChapterQualityError("chapter text must be a string")
    normalized = text.strip()
    if not normalized:
        raise ChapterQualityError("chapter text is empty")
    if "<!-- GENERATION_IN_PROGRESS -->" in normalized:
        raise ChapterQualityError("chapter still contains an in-progress marker")
    forbidden = (
        "写作元指令",
        "system prompt",
        "BEGIN_INSTRUCTION",
        "<|assistant|>",
        "<|system|>",
    )
    if any(marker.lower() in normalized.lower() for marker in forbidden):
        raise ChapterQualityError("chapter contains an instruction leak")
    minimum = max(120, min(600, int(max(1, target_words) * 0.25)))
    if len(normalized) < minimum:
        raise ChapterQualityError(f"chapter text is too short: {len(normalized)} < {minimum}")
    # A Chinese dialogue often ends with a closing quote/book-title bracket after
    # the actual punctuation, e.g. `……？"` or `……。」`.  The writer's ending
    # normalizer already treats these as complete; keep the quality gate aligned
    # instead of rejecting an otherwise valid generated chapter.
    sentence_endings = set("。！？.!?…")
    closing_marks = set("\"'”’」』】》〉〕）)]}")
    content_end = normalized
    while content_end and content_end[-1] in closing_marks:
        content_end = content_end[:-1]
    if not content_end or content_end[-1] not in sentence_endings:
        raise ChapterQualityError("chapter does not end with punctuation")
    return {"chars": len(normalized), "minimum_chars": minimum}
