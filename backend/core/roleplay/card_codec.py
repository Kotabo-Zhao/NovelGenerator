"""Roleplay mode (SillyTavern-style) — character card codec.

Supports:
- SillyTavern / TavernAI V2 & V3 cards embedded in PNG (tEXt / zTXt / iTXt
  chunks with keyword ``chara`` or ``ccv3``, base64 encoded JSON)
- plain JSON cards, V2/V3 envelopes (``{"spec": "chara_card_v2", "data": {...}}``)

Only stdlib is used so the module also works inside the Android (Chaquopy) app.
"""
from __future__ import annotations

import base64
import copy
import binascii
import json
import re
import struct
import zlib
from typing import Any, Dict, List

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_CARD_KEYWORDS = ("chara", "ccv3")

# Fields that identify a dict as a character payload rather than an envelope.
_CARD_FIELDS = (
    "name", "char_name", "description", "first_mes", "mes_example", "personality",
    "scenario", "system_prompt", "post_history_instructions", "alternate_greetings",
)


class CardError(ValueError):
    """Raised when a file is not a readable character card."""


def is_png(data: bytes) -> bool:
    return len(data) >= 8 and data[:8] == PNG_SIGNATURE


# ---------------------------------------------------------------- PNG parsing

def _iter_chunks(data: bytes):
    """Yield (chunk_type, payload) for every PNG chunk; stops on malformed data."""
    pos = 8
    total = len(data)
    while pos + 8 <= total:
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        chunk_type = data[pos + 4:pos + 8]
        start = pos + 8
        end = start + length
        if end > total:
            return
        yield chunk_type, data[start:end]
        pos = end + 4  # skip CRC


_MAX_TEXT_CHUNK_BYTES = 8 * 1024 * 1024


def _decompress_limited(data: bytes, limit: int = _MAX_TEXT_CHUNK_BYTES) -> bytes:
    """Bounded zlib decompression (guards against PNG text-chunk bombs)."""
    decompressor = zlib.decompressobj()
    output = decompressor.decompress(data, limit + 1)
    if len(output) > limit or decompressor.unconsumed_tail:
        raise ValueError("PNG 文本块解压后过大")
    return output


def _decode_text_chunk(chunk_type: bytes, payload: bytes) -> tuple[str, str] | None:
    """Decode one textual PNG chunk into (keyword, text); None if unsupported."""
    try:
        if chunk_type == b"tEXt":
            keyword, _, text = payload.partition(b"\x00")
            return keyword.decode("latin-1"), text.decode("latin-1")

        if chunk_type == b"zTXt":
            keyword, _, rest = payload.partition(b"\x00")
            if not rest:
                return None
            compressed = rest[1:]  # first byte = compression method (0)
            return keyword.decode("latin-1"), _decompress_limited(compressed).decode("utf-8")

        if chunk_type == b"iTXt":
            keyword, _, rest = payload.partition(b"\x00")
            if len(rest) < 2:
                return None
            compression_flag = rest[0]
            rest = rest[2:]  # skip flag + compression method
            _language, _, rest = rest.partition(b"\x00")
            _translated, _, text = rest.partition(b"\x00")
            if compression_flag:
                text = _decompress_limited(text)
            return keyword.decode("latin-1"), text.decode("utf-8")
    except (zlib.error, UnicodeDecodeError, ValueError):
        return None
    return None


def _b64decode_text(value: str) -> str:
    cleaned = "".join(value.split())
    if not cleaned:
        raise CardError("角色卡数据为空")
    padding = "=" * (-len(cleaned) % 4)
    try:
        raw = base64.b64decode(cleaned + padding, validate=False)
    except (binascii.Error, ValueError) as e:
        raise CardError(f"角色卡 base64 解码失败: {e}") from e
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise CardError("角色卡 JSON 不是 UTF-8 编码") from e


def card_json_from_png(data: bytes) -> str:
    """Extract the base64-decoded card JSON text from a PNG character card."""
    if not is_png(data):
        raise CardError("不是有效的 PNG 文件")
    chara_text: str | None = None
    ccv3_text: str | None = None
    for chunk_type, payload in _iter_chunks(data):
        if chunk_type == b"IEND":
            break
        if chunk_type not in (b"tEXt", b"zTXt", b"iTXt"):
            continue
        decoded = _decode_text_chunk(chunk_type, payload)
        if not decoded:
            continue
        keyword, text = decoded
        key = keyword.strip().lower()
        if key == "chara" and chara_text is None:
            chara_text = text
        elif key == "ccv3":
            ccv3_text = text
    chosen = ccv3_text or chara_text
    if not chosen:
        raise CardError("这张 PNG 里没有 SillyTavern 角色卡数据（chara/ccv3）")
    return _b64decode_text(chosen)


# --------------------------------------------------------------- JSON parsing

def card_json_from_bytes(data: bytes) -> tuple[str, str]:
    """Return (json_text, format) for PNG or JSON card bytes."""
    if is_png(data):
        return card_json_from_png(data), "png"
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise CardError("文件不是 UTF-8 编码的 JSON 或 PNG") from e
    stripped = text.lstrip()
    if not stripped.startswith("{"):
        raise CardError("无法识别的角色卡格式（需要 SillyTavern PNG 或 JSON）")
    return text, "json"


# --------------------------------------------------------------- normalizing

def _text(src: Dict[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        value = src.get(key)
        if isinstance(value, (int, float)):
            value = str(value)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return default


def _str_list(value: Any) -> List[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def normalize_card(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten a V2/V3 envelope or a bare card into the fields we persist."""
    if not isinstance(raw, dict):
        raise CardError("角色卡 JSON 顶层必须是对象")

    # Public shop exports can wrap the definition rather than a V2 data object.
    if isinstance(raw.get('node'), dict) and 'definition' in raw['node']:
        raw = shop_card_payload(raw['node'])
    elif 'definition' in raw:
        raw = shop_card_payload(raw)
    elif isinstance(raw.get('character'), dict):
        raw = raw['character']

    spec = str(raw.get("spec") or "")
    data = raw.get("data")
    if isinstance(data, dict) and (
        spec.startswith("chara_card")
        or any(field in data for field in _CARD_FIELDS)
    ):
        src = data
    else:
        src = raw

    name = _text(src, "name", "char_name", default="未命名角色")[:80]
    card = {
        "name": name,
        "description": _text(src, "description", "char_persona"),
        "personality": _text(src, "personality"),
        "scenario": _text(src, "scenario", "world_scenario"),
        "first_mes": _text(src, "first_mes", "first_message", "char_greeting"),
        "mes_example": _text(src, "mes_example", "example_dialogue", "example_dialogs"),
        "system_prompt": _text(src, "system_prompt"),
        "post_history_instructions": _text(src, "post_history_instructions"),
        "alternate_greetings": _str_list(src.get("alternate_greetings")),
        "tags": _str_list(src.get("tags")),
        "creator": _text(src, "creator", "created_by"),
        "character_version": _text(src, "character_version"),
        "creator_notes": _text(src, "creator_notes"),
        "extensions": src.get("extensions") if isinstance(src.get("extensions"), dict) else {},
        # Preserve V3 and vendor fields for lossless editing/export. Never execute them.
        "card_data": copy.deepcopy(src),
        "card_spec": spec or 'chara_card_v2',
        "card_spec_version": str(raw.get('spec_version') or '2.0'),
        # V3 character_book (lorebook) is extracted by the API layer, not persisted
        # on the card itself.
        "character_book": (
            src.get("character_book") if isinstance(src.get("character_book"), dict)
            else raw.get("character_book") if isinstance(raw.get("character_book"), dict)
            else {}
        ),
    }
    return card


def shop_card_payload(node: Dict[str, Any]) -> Dict[str, Any]:
    """Convert either Chub's legacy definition or a standard V2/V3 definition."""
    definition = node.get('definition')
    if isinstance(definition, str):
        try:
            definition = json.loads(definition)
        except json.JSONDecodeError as exc:
            raise CardError('商店角色定义不是有效 JSON') from exc
    if not isinstance(definition, dict):
        raise CardError('商店角色缺少有效的 definition')
    envelope = isinstance(definition.get('data'), dict)
    data = copy.deepcopy(definition['data'] if envelope else definition)
    # Chub's legacy API calls its full definition "personality" and its
    # listing blurb "description". Greeting/lorebook aliases alone do NOT
    # identify that schema: standard cards also commonly carry these aliases.
    legacy = not envelope and 'tavern_personality' in data and 'first_mes' not in data
    if legacy:
        data.update(description=data.get('personality', ''),
                    personality=data.get('tavern_personality', ''),
                    creator_notes=data.get('creator_notes', data.get('description', '')))
    aliases = {'first_mes':'first_message', 'mes_example':'example_dialogs', 'character_book':'embedded_lorebook'}
    for target, alias in aliases.items():
        if target not in data and alias in data:
            data[target] = copy.deepcopy(data[alias])
    book = data.get('character_book')
    if isinstance(book, str):
        try:
            data['character_book'] = json.loads(book)
        except json.JSONDecodeError as exc:
            raise CardError('商店内嵌世界书不是有效 JSON') from exc
    data.setdefault('name', node.get('name', '未命名角色'))
    data.setdefault('tags', node.get('topics') or [])
    data.setdefault('creator', '')
    result = copy.deepcopy(definition) if envelope else {'spec':'chara_card_v2', 'spec_version':'2.0'}
    result['data'] = data
    return result


def parse_card_bytes(data: bytes, filename: str = "") -> tuple[Dict[str, Any], str]:
    """Parse card bytes -> (normalized card, format). filename reserved for errors."""
    json_text, fmt = card_json_from_bytes(data)
    try:
        raw = json.loads(json_text)
    except json.JSONDecodeError as e:
        raise CardError(f"角色卡 JSON 解析失败: {e}") from e
    if not isinstance(raw, dict):
        raise CardError("角色卡 JSON 顶层必须是对象")
    return normalize_card(raw), fmt


# ------------------------------------------------------------------- macros

def apply_macros(text: str, char_name: str, user_name: str, context=None, original='') -> str:
    """Replace SillyTavern macros used in cards."""
    if not text:
        return ""
    values = {str(k).lower(): str(v or '') for k, v in (context or {}).items()}
    values.update(char=char_name, bot=char_name, user=user_name, original=original,
                  newline='\n', space=' ', noop='', trim='')
    text = re.sub(r'<(?:BOT|USER)>', lambda m: char_name if m[0].upper() == '<BOT>' else user_name,
                  str(text), flags=re.I)
    pattern = r'\{\{\s*([^{}]+?)\s*\}\}'
    for _ in range(4):
        replaced = re.sub(pattern, lambda m: values.get(m[1].lower(), m[0]), text)
        if replaced == text:
            break
        text = replaced[:500000]
    return text


def macro_context(card, settings, history=()):
    context = {
        'description': card.get('description'), 'charDescription': card.get('description'),
        'personality': card.get('personality'), 'charPersonality': card.get('personality'),
        'scenario': card.get('scenario'), 'charScenario': card.get('scenario'),
        'persona': settings.get('user_persona'), 'charCreatorNotes': card.get('creator_notes'),
        'firstMessage': card.get('first_mes'), 'charExamples': card.get('mes_example'),
        'mesExamples': card.get('mes_example'), 'charVersion': card.get('character_version'),
        'lastMessageId': str(max(0, len(history) - 1)),
    }
    for macro, role in [('lastMessage', None), ('lastUserMessage', 'user'), ('lastCharMessage', 'assistant')]:
        context[macro] = next((m.get('content', '') for m in reversed(history) if role is None or m.get('role') == role), '')
    return context


def pick_greeting(card: Dict[str, Any], index: int | None = None) -> str:
    """Choose a greeting: explicit index, first_mes, alternate, or fallback."""
    greetings: List[str] = []
    first = str(card.get("first_mes") or "").strip()
    if first:
        greetings.append(first)
    greetings.extend(str(g).strip() for g in (card.get("alternate_greetings") or []) if str(g).strip())
    if not greetings:
        return f"*{card.get('name', '角色')}看着你，安静地等你开口。*"
    if index is not None:
        try:
            return greetings[int(index) % len(greetings)]
        except (ValueError, TypeError, IndexError):
            pass
    return greetings[0]
