"""Deterministic pre-persistence checks for generated chapter text."""

from __future__ import annotations

import re


class ChapterQualityError(ValueError):
    pass


_DELIMITER_PAIRS = {
    "「": "」",
    "『": "』",
    "“": "”",
    "（": "）",
    "【": "】",
    "〔": "〕",
    "《": "》",
    "〈": "〉",
    "[": "]",
    "{": "}",
}
_DELIMITER_CLOSERS = {closer: opener for opener, closer in _DELIMITER_PAIRS.items()}


def _validate_balanced_delimiters(text: str) -> None:
    stack: list[tuple[str, int]] = []
    for index, char in enumerate(text):
        if char in _DELIMITER_PAIRS:
            stack.append((char, index))
            continue
        if char not in _DELIMITER_CLOSERS:
            continue
        expected_opener = _DELIMITER_CLOSERS[char]
        if not stack or stack[-1][0] != expected_opener:
            raise ChapterQualityError(
                f"chapter contains an unbalanced delimiter: unexpected {char} at {index}"
            )
        stack.pop()

    if stack:
        opener, index = stack[-1]
        raise ChapterQualityError(
            f"chapter contains an unbalanced delimiter: unclosed {opener} at {index}"
        )


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
    _validate_balanced_delimiters(normalized)
    # v2.7.6: 硬底 120→50——字数放开后小目标（超短篇100-300字）不得被卡死；
    # 正常目标(≥1000字)时 0.25×目标 ≥250，公式对长篇行为完全不变。
    minimum = max(50, min(600, int(max(1, target_words) * 0.25)))
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
    return {
        "chars": len(normalized),
        "minimum_chars": minimum,
        "delimiters_balanced": True,
    }
