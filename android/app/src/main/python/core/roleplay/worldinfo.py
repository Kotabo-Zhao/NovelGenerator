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
import copy
import random
import re
import time
import regex
import uuid
from typing import Any, Dict, Iterable, List, Optional

MAX_ENTRIES = 500
MAX_ENTRY_CHARS = 500000
MAX_PROMPT_CHARS = 64000

_LOGIC_AND_ANY = 0
_LOGIC_AND_ALL = 3
_LOGIC_NOT_ANY = 2
_LOGIC_NOT_ALL = 1


def _str_list(value: Any) -> List[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def normalize_entry(raw: Dict[str, Any], index: int = 0) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("世界书条目不合法")
    entry_id = raw.get("id", raw.get("uid"))
    if entry_id is None or str(entry_id).strip() == "":
        entry_id = f"wi_{uuid.uuid4().hex[:12]}"
    keys = _str_list(raw.get("keys", raw.get("key")))
    secondary = _str_list(raw.get("secondary_keys", raw.get("keysecondary")))
    content = str(raw.get("content", raw.get("text", "")) or "")[:MAX_ENTRY_CHARS]
    comment = str(raw.get("comment", raw.get("name", "")) or "").strip()[:200]
    try:
        selective_logic = int(raw.get("selectiveLogic", raw.get("selective_logic", _LOGIC_AND_ANY)) or 0)
    except (TypeError, ValueError):
        selective_logic = _LOGIC_AND_ANY
    extensions = raw.get('extensions') if isinstance(raw.get('extensions'), dict) else {}
    def option(*keys, default=None):
        for source in (raw, extensions):
            for key in keys:
                if source.get(key) is not None:
                    return source[key]
        return default
    try:
        selective_logic = int(option('selective_logic', 'selectiveLogic', default=0))
    except (TypeError, ValueError):
        selective_logic = 0
    try:
        order = int(option('order', 'insertion_order', default=100))
    except (TypeError, ValueError):
        order = 100
    try:
        depth = int(option('depth', default=4))
    except (TypeError, ValueError):
        depth = 4
    try:
        probability = int(option('probability', default=100))
    except (TypeError, ValueError):
        probability = 100
    enabled_raw = raw.get("enabled")
    enabled = True if enabled_raw is None else bool(enabled_raw)
    if enabled_raw is None and raw.get("disable"):
        enabled = False
    positions = {0:'before_char', 1:'after_char', 2:'before_note', 3:'after_note',
                 4:'at_depth', 5:'before_examples', 6:'after_examples', 7:'outlet'}
    position = raw.get('position', 'before_char')
    if isinstance(position, str) and position.isdigit():
        position = int(position)
    # V2's before/after flag can coexist with ST's more precise numeric extension.
    if position in ('before_char', 'after_char') and isinstance(extensions.get('position'), int):
        position = positions.get(extensions['position'], position)
    position = positions.get(position, position) if isinstance(position, (int, str)) else 'before_char'
    role = option('role', default='system')
    role = {0:'system', 1:'user', 2:'assistant'}.get(role, role) if isinstance(role, (int, str)) else 'system'
    return {
        **copy.deepcopy(raw),
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
        "position": str(position)[:40],
        "role": role if role in ('system', 'user', 'assistant') else 'system',
        "case_sensitive": bool(option('case_sensitive', 'caseSensitive', default=False)),
        "match_whole_words": bool(option('match_whole_words', 'matchWholeWords', default=False)),
        "exclude_recursion": bool(option('exclude_recursion', 'excludeRecursion', default=False)),
        "prevent_recursion": bool(option('prevent_recursion', 'preventRecursion', default=False)),
        "use_probability": bool(option('use_probability', 'useProbability', default=True)),
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
        raw_entries = [dict(value, id=value.get('id', value.get('uid', key)))
                       for key, value in raw_entries.items() if isinstance(value, dict)]
    if not isinstance(raw_entries, list):
        raise ValueError("不是有效的 SillyTavern 世界书 JSON（缺少 entries）")

    entries: List[Dict[str, Any]] = []
    seen: set = set()
    if len(raw_entries) > MAX_ENTRIES:
        raise ValueError(f"世界书最多支持 {MAX_ENTRIES} 条，请拆分后导入；本次未导入任何条目")
    for index, raw in enumerate(raw_entries):
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


def decode_worldinfo_bytes(data: bytes) -> Any:
    try:
        encoding = 'utf-16' if data.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
        text = data.decode(encoding)
    except UnicodeDecodeError as exc:
        raise ValueError("世界书必须是 UTF-8 编码的 JSON") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"世界书 JSON 解析失败: {exc}") from exc
    return payload


def import_worldinfo_bytes(data: bytes, filename: str = "") -> List[Dict[str, Any]]:
    return parse_worldinfo_payload(decode_worldinfo_bytes(data))


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


def _matches(haystack: str, needle: str, case_sensitive: bool = False, whole_words: bool = False) -> bool:
    if not needle:
        return False
    if needle.startswith('/') and needle.rfind('/') > 0:
        end = needle.rfind('/')
        flags = needle[end + 1:]
        if not set(flags) - set('gimsud') and len(needle) <= 12000:
            options = regex.VERSION1
            for flag, option in [('i', regex.I), ('m', regex.M), ('s', regex.S)]:
                if flag in flags:
                    options |= option
            try:
                return bool(regex.search(needle[1:end], haystack, flags=options, timeout=.01))
            except (regex.error, TimeoutError):
                return False
    if whole_words:
        return bool(re.search(r'(?<!\w)' + re.escape(needle) + r'(?!\w)', haystack, 0 if case_sensitive else re.I))
    return needle in haystack if case_sensitive else needle.lower() in haystack.lower()


def scan_world_info(
    entries: Iterable[Dict[str, Any]],
    messages: List[Dict[str, Any]],
    input_text: str = "",
    *,
    budget: int = 5,
    scan_depth: int = 6,
    recursive: bool = False,
    token_budget: int = 0,
) -> List[Dict[str, Any]]:
    """Return activated entries, ordered by ST ``order`` (descending)."""
    history = [
        str(msg.get("content") or "")
        for msg in messages
        if isinstance(msg, dict) and str(msg.get("content") or "").strip()
    ][-max(1, scan_depth):] if scan_depth else []
    haystack = "\n".join(history + [str(input_text or "")])
    activated: List[Dict[str, Any]] = []
    entries = list(entries)
    regex_deadline = time.monotonic() + .15
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("enabled", True):
            continue
        content = str(entry.get("content") or "").strip()
        if not content:
            continue
        primary = [str(key) for key in entry.get("keys") or [] if str(key).strip()]
        secondary = [str(key) for key in entry.get("secondary_keys") or [] if str(key).strip()]
        def matches(key):
            if key.startswith('/') and time.monotonic() > regex_deadline:
                return False
            return _matches(haystack, key, entry.get('case_sensitive', False), entry.get('match_whole_words', False))

        if entry.get("constant"):
            pass
        elif not primary:
            continue
        elif not any(matches(key) for key in primary):
            continue
        elif entry.get("selective") and secondary:
            logic = int(entry.get("selective_logic") or 0)
            if logic == _LOGIC_AND_ANY:
                # primary AND at least one secondary (ST AND_ANY)
                if not any(matches(key) for key in secondary):
                    continue
            elif logic == _LOGIC_AND_ALL:
                if not all(matches(key) for key in secondary):
                    continue
            elif logic == _LOGIC_NOT_ANY:
                if any(matches(key) for key in secondary):
                    continue
            elif logic == _LOGIC_NOT_ALL:
                if all(matches(key) for key in secondary):
                    continue

        probability = int(entry.get("probability", 100) or 0)
        if entry.get('use_probability', True) and probability < 100 and random.randint(1, 100) > probability:
            continue
        activated.append(entry)

    if recursive:
        # Only activated, recursion-enabled contents may trigger another entry.
        seen = {str(e.get('id')) for e in activated}
        for _ in range(min(len(entries), 20)):
            extra = '\n'.join(str(e.get('content', '')) for e in activated if not e.get('prevent_recursion'))
            pending = [e for e in entries if str(e.get('id')) not in seen and not e.get('exclude_recursion')]
            new = scan_world_info(pending, messages, input_text + '\n' + extra,
                                  budget=MAX_ENTRIES, scan_depth=scan_depth)
            if not new:
                break
            activated.extend(new)
            seen.update(str(e.get('id')) for e in new)

    activated.sort(key=lambda item: int(item.get("order", 100) or 100), reverse=True)
    total = 0
    capped: List[Dict[str, Any]] = []
    for entry in activated:
        if len(capped) >= max(1, min(budget, MAX_ENTRIES)):
            break
        content = str(entry.get("content") or "")
        remaining = MAX_PROMPT_CHARS - total
        if remaining <= 0:
            break
        if len(content) > remaining:
            if capped:
                continue
            content = content[:remaining]
        if token_budget and sum(estimate_tokens(str(e.get('content', ''))) for e in capped) + estimate_tokens(content) > token_budget:
            continue
        item = dict(entry)
        item["content"] = content
        capped.append(item)
        total += len(content)
    return capped


def estimate_tokens(text):
    """Conservative mixed CJK/Latin estimate; provider tokenizers differ."""
    cjk = len(re.findall(r'[\u3400-\u9fff]', text))
    return cjk + (len(text) - cjk + 3) // 4


def format_world_info(entries: List[Dict[str, Any]]) -> str:
    if not entries:
        return ""
    lines = ["[世界书]"]
    for entry in sorted(entries, key=lambda item: int(item.get('order', 100))):
        title = str(entry.get("comment") or "").strip()
        content = str(entry.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"### {title}\n{content}" if title else content)
    return "\n\n".join(lines)


def merge_entries(existing: List[Dict[str, Any]], incoming: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Book-local numeric UIDs must never overwrite another book's entries."""
    result = copy.deepcopy(existing)
    ids = {str(entry.get('id')) for entry in result}
    def signature(entry):
        value = {k: v for k, v in normalize_entry(entry).items() if k not in ('id', 'uid')}
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    known = {signature(entry) for entry in result}
    for raw in incoming:
        entry = copy.deepcopy(raw)
        fingerprint = signature(entry)
        if fingerprint in known:
            continue
        if str(entry.get('id')) in ids:
            entry['id'] = f"wi_{uuid.uuid4().hex[:12]}"
        ids.add(str(entry['id']))
        known.add(fingerprint)
        result.append(entry)
    if len(result) > MAX_ENTRIES:
        raise ValueError(f"合并后超过 {MAX_ENTRIES} 条，本次未导入；请先整理已有世界书")
    return result


def export_entries(entries):
    """Keep V2 before/after plus ST-specific positions in the extensions bag."""
    positions = {'before_char':0, 'after_char':1, 'before_note':2, 'after_note':3,
                 'at_depth':4, 'before_examples':5, 'after_examples':6, 'outlet':7}
    result = []
    for index, entry in enumerate(entries):
        item = copy.deepcopy(entry)
        extension = dict(item.get('extensions') or {})
        position = item.get('position', 'before_char')
        extension.update(position=positions.get(position, 0), depth=item.get('depth', 4),
                         role={'system':0, 'user':1, 'assistant':2}.get(item.get('role'), 0))
        # V2 entry IDs are numeric. Legacy aliases must reflect current edits, too.
        item.update(id=index, uid=index, key=item.get('keys', []),
                    keysecondary=item.get('secondary_keys', []), disable=not item.get('enabled', True),
                    selectiveLogic=item.get('selective_logic', 0),
                    position='before_char' if position == 'before_char' else 'after_char',
                    insertion_order=item.get('order', 100), extensions=extension)
        result.append(item)
    return result
