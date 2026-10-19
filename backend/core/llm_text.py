"""Extract visible text without ever stringifying provider protocol objects."""
from __future__ import annotations

import json
from collections.abc import Mapping


def extract_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "".join(extract_text(part) for part in value)
    if not isinstance(value, Mapping):
        if hasattr(value, "model_dump"):
            value = value.model_dump()
        elif hasattr(value, "choices"):
            choices = value.choices
            return extract_text(choices[0].delta) if choices else ""
        elif hasattr(value, "content"):
            return extract_text(value.content)
        else:
            raise TypeError("模型返回了无法识别的文本格式")
    # Tool calls, reasoning, usage, and image blocks are never dialogue text.
    kind = value.get("type")
    if kind and kind not in ("text", "output_text", "text_delta", "delta"):
        return ""
    if "choices" in value:
        choices = value["choices"] or []
        if not choices:
            return ""
        choice = choices[0]
        return extract_text(choice.get("delta", choice.get("message", {})))
    for key in ("content", "text", "delta"):
        if key in value:
            return extract_text(value[key])
    return ""


def display_text(value) -> str:
    """Read older serialized content conservatively; leave ordinary prose intact."""
    text = extract_text(value)
    candidate = text.strip()
    if candidate.startswith('{'):
        try:
            payload = json.loads(candidate)
            if isinstance(payload, dict) and ("choices" in payload or "content" in payload):
                return extract_text(payload)
        except (ValueError, TypeError):
            pass
    return text.replace("\r\n", "\n").replace("\x00", "")
