"""SillyTavern world-info / lorebook support.

Accepts ST world-info JSON in its common shapes:

* ``{"entries": {"0": {...}, "12": {...}}}`` (exported lorebook)
* ``{"entries": [{...}, ...]}``
* ``{"character_book": {"entries": [...]}}`` (embedded in character cards)
* a bare list of entries

Entries are normalised to::

    {"id","comment","keys","secondary_keys","content","enabled","constant",
     "selective","selective_logic","order","position","depth","probability"}

Activation mirrors ST's basics: constant entries always trigger, otherwise
keys are matched (case-insensitive substring) against the recent chat plus the
current input; ``selective`` entries additionally require the secondary-key
logic (AND_ANY / AND_ALL / NOT_ANY / NOT_ALL), then a probability roll.
"""
from __future__ import annotations

import json
import random
import re
import uuid
from typing import Any, Dict, Iterable, List, Optional

MAX_ENTRIES = 500
MAX_ENTRY_CHARS = 20000
MAX_PROMPT_CHARS = 6000

_LOGIC_AND_ANY = 0
_LOGIC_AND_ALL = 1
_LOGIC_NOT_ANY = 2
_LOGIC_NOT_ALL = 3


def _str_list(value: Any) -> List[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def normalize_entry(raw: Dict[str, Any], index: int = 0) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("世界书条目不合法")
    entry_id = raw.get("uid", raw.get("id"))
    if entry_id is None or str(entry_id).strip() == "":
        entry_id = f"wi_{uuid.uuid4().hex[:12]}"
    keys = _str_list(raw.get("key", raw.get("keys")))
    secondary = _str_list(raw.get("keysecondary", raw.get("secondary_keys")))
    content = str(raw.get("content", raw.get("text", "")) or "")[:MAX_ENTRY_CHARS]
    comment = str(raw.get("comment", raw.get("name", "")) or "").strip()[:200]
    try:
        selective_logic = int(raw.get("selectiveLogic", raw.get("selective_logic", _LOGIC_AND_ANY)) or 0)
    except (TypeError, ValueError):
        selective_logic = _LOGIC_AND_ANY
    try:
        order = int(raw.get("order", 100) or 100)
    except (TypeError, ValueError):
        order = 100
    try:
        depth = int(raw.get("depth", 4) or 4)
    except (TypeError, ValueError):
        depth = 4
    try:
        probability = int(raw.get("probability", 100) if raw.get("probability") is not None else 100)
    except (TypeError, ValueError):
        probability = 100
    enabled_raw = raw.get("enabled")
    enabled = True if enabled_raw is None else bool(enabled_raw)
    if raw.get("disable"):
        enabled = False
    return {
        "id": str(entry_id)[:80],
        "comment": comment or ", ".join(keys[:2]) or f"条目 {index + 1}",
        "keys": keys[:50],
        "secondary_keys": secondary[:50],
        "content": content,
        "enabled": enabled,
        "constant": bool(raw.get("constant")),
        "selective": bool(raw.get("selective", bool(secondary))),
        "selective_logic": selective_logic if selective_logic in (0, 1, 2, 3) else 0,
        "order": max(-10000, min(10000, order)),
        "position": str(raw.get("position", "before_char"))[:40],
        "depth": max(0, min(100, depth)),
        "probability": max(0, min(100, probability)),
    }


def parse_worldinfo_payload(payload: Any) -> List[Dict[str, Any]]:
    """Extract and normalise entries from any supported ST world-info shape."""
    raw_entries: Any = None
    if isinstance(payload, list):
        raw_entries = payload
    elif isinstance(payload, dict):
        if isinstance(payload.get("entries"), (dict, list)):
            raw_entries = payload["entries"]
        elif isinstance(payload.get("character_book"), dict):
            book = payload["character_book"]
            raw_entries = book.get("entries")
        elif isinstance(payload.get("data"), dict) and isinstance(payload["data"].get("character_book"), dict):
            raw_entries = payload["data"]["character_book"].get("entries")
        elif isinstance(payload.get("data"), dict) and isinstance(payload["data"].get("entries"), (dict, list)):
            raw_entries = payload["data"]["entries"]
    if isinstance(raw_entries, dict):
        raw_entries = list(raw_entries.values())
    if not isinstance(raw_entries, list):
        raise ValueError("不是有效的 SillyTavern 世界书 JSON（缺少 entries）")

    entries: List[Dict[str, Any]] = []
    seen: set = set()
    for index, raw in enumerate(raw_entries[:MAX_ENTRIES]):
        if not isinstance(raw, dict):
            continue
        try:
            entry = normalize_entry(raw, index)
        except ValueError:
            continue
        if entry["id"] in seen:
            entry["id"] = f"{entry['id']}_{index}"
        seen.add(entry["id"])
        entries.append(entry)
    if not entries:
        raise ValueError("世界书里没有可导入的条目")
    return entries


def import_worldinfo_bytes(data: bytes, filename: str = "") -> List[Dict[str, Any]]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("世界书必须是 UTF-8 编码的 JSON") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"世界书 JSON 解析失败: {exc}") from exc
    return parse_worldinfo_payload(payload)


def entries_from_card(card: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract embedded character_book entries from an imported ST card."""
    if not isinstance(card, dict):
        return []
    book = card.get("character_book")
    if isinstance(book, dict):
        try:
            return parse_worldinfo_payload(book)
        except ValueError:
            return []
    return []


def _matches(haystack: str, needle: str, case_sensitive: bool = False) -> bool:
    if not needle:
        return False
    if case_sensitive:
        return needle in haystack
    return needle.lower() in haystack.lower()


def scan_world_info(
    entries: Iterable[Dict[str, Any]],
    messages: List[Dict[str, Any]],
    input_text: str = "",
    *,
    budget: int = 5,
    scan_depth: int = 6,
) -> List[Dict[str, Any]]:
    """Return activated entries, ordered by ST ``order`` (descending)."""
    history = [
        str(msg.get("content") or "")
        for msg in messages
        if isinstance(msg, dict) and str(msg.get("content") or "").strip()
    ][-max(1, scan_depth):]
    haystack = "\n".join(history + [str(input_text or "")])
    activated: List[Dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("enabled", True):
            continue
        content = str(entry.get("content") or "").strip()
        if not content:
            continue
        primary = [str(key) for key in entry.get("keys") or [] if str(key).strip()]
        secondary = [str(key) for key in entry.get("secondary_keys") or [] if str(key).strip()]

        if entry.get("constant"):
            pass
        elif not primary:
            continue
        elif not any(_matches(haystack, key) for key in primary):
            continue
        elif entry.get("selective") and secondary:
            matches = [any(_matches(haystack, key) for key in secondary)]
            logic = int(entry.get("selective_logic") or 0)
            if logic == _LOGIC_AND_ANY:
                # primary AND at least one secondary (ST AND_ANY)
                if not any(_matches(haystack, key) for key in secondary):
                    continue
            elif logic == _LOGIC_AND_ALL:
                if not all(_matches(haystack, key) for key in secondary):
                    continue
            elif logic == _LOGIC_NOT_ANY:
                if any(_matches(haystack, key) for key in secondary):
                    continue
            elif logic == _LOGIC_NOT_ALL:
                if all(_matches(haystack, key) for key in secondary):
                    continue

        probability = int(entry.get("probability", 100) or 0)
        if probability < 100 and random.randint(1, 100) > probability:
            continue
        activated.append(entry)

    activated.sort(key=lambda item: int(item.get("order", 100) or 100), reverse=True)
    total = 0
    capped: List[Dict[str, Any]] = []
    for entry in activated:
        if len(capped) >= max(1, min(budget, 50)):
            break
        content = str(entry.get("content") or "")
        remaining = MAX_PROMPT_CHARS - total
        if remaining <= 0:
            break
        if len(content) > remaining:
            if capped:
                continue
            content = content[:remaining]
        item = dict(entry)
        item["content"] = content
        capped.append(item)
        total += len(content)
    return capped


def format_world_info(entries: List[Dict[str, Any]]) -> str:
    if not entries:
        return ""
    lines = ["[世界书]"]
    for entry in entries:
        title = str(entry.get("comment") or "").strip()
        content = str(entry.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"### {title}\n{content}" if title else content)
    return "\n\n".join(lines)


def merge_entries(existing: List[Dict[str, Any]], incoming: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Append imported entries, replacing same-id entries."""
    by_id = {str(entry.get("id")): entry for entry in existing}
    for entry in incoming:
        by_id[str(entry.get("id"))] = entry
    return list(by_id.values())[:MAX_ENTRIES]
