"""Deterministic quality gate, ending sanitizer, and continuation deduplicator."""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class ChapterQualityError(ValueError):
    """Raised when generated prose violates hard structural safety invariants."""


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
_DELIMITER_CLOSERS = {v: k for k, v in _DELIMITER_PAIRS.items()}
_FORBIDDEN_MARKERS = (
    "写作元指令",
    "system prompt",
    "BEGIN_INSTRUCTION",
    "<|assistant|>",
    "<|system|>",
    "<!-- GENERATION_IN_PROGRESS -->",
)


def delimiters_balanced(text: str) -> bool:
    stack: list[str] = []
    for ch in text:
        if ch in _DELIMITER_PAIRS:
            stack.append(ch)
        elif ch in _DELIMITER_CLOSERS:
            if not stack or stack[-1] != _DELIMITER_CLOSERS[ch]:
                return False
            stack.pop()
    return not stack


def dedup_continuation(existing: str, continuation: str) -> str:
    """Remove overlapping suffix/prefix or sentence echoes when continuing a draft."""
    if not continuation:
        return ""
    existing_clean = existing.rstrip()
    cont_stripped = continuation.lstrip()
    if not existing_clean or not cont_stripped:
        return continuation

    window = min(80, len(existing_clean))
    tail = existing_clean[-window:]

    for cut_len in range(min(window, len(cont_stripped)), 3, -1):
        if tail[-cut_len:] == cont_stripped[:cut_len]:
            return cont_stripped[cut_len:]

    for lead in range(1, min(4, len(cont_stripped))):
        sub = cont_stripped[lead:]
        for cut_len in range(min(window, len(sub)), 7, -1):
            if tail[-cut_len:] == sub[:cut_len]:
                return sub[cut_len:]

    return continuation


def ensure_complete_ending(text: str) -> str:
    """Trim trailing half-sentences while preserving balanced Chinese dialogue quotes."""
    clean = (text or "").rstrip()
    if len(clean) < 80:
        return clean

    sentence_endings = set("。！？.!?…~")
    closing_marks = set("\"'”’」』】》〉〕）)]}")

    inner = clean
    while inner and inner[-1] in closing_marks:
        inner = inner[:-1]
    if inner and inner[-1] in sentence_endings:
        return clean
    if clean[-1] in closing_marks and inner:
        return clean + "。"

    search_start = max(0, len(clean) - 320)
    tail = clean[search_start:]
    last_pos = -1
    for marker in "。！？!?…":
        pos = tail.rfind(marker)
        if pos > last_pos:
            last_pos = pos

    if last_pos >= 0:
        cut_end = search_start + last_pos + 1
        while cut_end < len(clean) and clean[cut_end] in closing_marks:
            cut_end += 1
        candidate = clean[:cut_end].rstrip()
        if delimiters_balanced(candidate):
            return candidate
    return clean


def validate_chapter_text(text: str, *, target_words: int = 1500) -> dict:
    """Validate structural integrity before committing chapter prose."""
    if not isinstance(text, str) or not text.strip():
        raise ChapterQualityError("章节正文为空")
    normalized = text.strip()
    lower = normalized.lower()
    for marker in _FORBIDDEN_MARKERS:
        if marker.lower() in lower:
            raise ChapterQualityError(f"正文包含非法元指令泄漏: {marker}")

    if not delimiters_balanced(normalized):
        raise ChapterQualityError("正文存在未闭合或错配的引号/括号")

    minimum = max(50, min(500, int(max(1, target_words) * 0.2)))
    if len(normalized) < minimum:
        raise ChapterQualityError(f"正文字数过短 ({len(normalized)} < {minimum})")

    sentence_endings = set("。！？.!?…")
    closing_marks = set("\"'”’」』】》〉〕）)]}")
    content_end = normalized
    while content_end and content_end[-1] in closing_marks:
        content_end = content_end[:-1]
    if not content_end or content_end[-1] not in sentence_endings:
        raise ChapterQualityError("章节未以完整标点结尾")

    return {
        "chars": len(normalized),
        "minimum_chars": minimum,
        "delimiters_balanced": True,
    }
