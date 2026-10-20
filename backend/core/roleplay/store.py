"""Roleplay mode storage — characters, avatars, chats and settings.

Data lives outside ``NOVELS_DIR`` so it never shows up as a "novel" in the
shelf:

    <ROLEPLAY_DIR>/
        characters.json
        settings.json
        avatars/<character_id>.png
        chats/<character_id>.json
"""
from __future__ import annotations

import copy
import os
import re
import shutil
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from config import NOVELS_DIR
from ..atomic_io import atomic_write_json, safe_read_json
from . import worldinfo
from .identity import IDENTITY_DEFAULTS, normalize_identity

DEFAULT_SETTINGS: Dict[str, Any] = {
    "user_name": "旅行者",
    "user_persona": "",
    "system_prompt": "",
    "temperature": 0.85,
    "max_tokens": 600,
    "context_messages": 40,
    "authors_note": "",
    "authors_note_depth": 4,
    "lorebook_enabled": True,
    "lorebook_budget": 5,
    "lorebook_scan_depth": 6,
}

_CARD_DEFAULTS: Dict[str, Any] = {
    "id": "",
    "name": "未命名角色",
    "description": "",
    "personality": "",
    "scenario": "",
    "first_mes": "",
    "mes_example": "",
    "system_prompt": "",
    "post_history_instructions": "",
    "alternate_greetings": [],
    "tags": [],
    "creator": "",
    "character_version": "",
    "creator_notes": "",
    "extensions": {},
    "source": "manual",
    "novel_id": "",
    "created_at": 0,
    "updated_at": 0,
}

_BUILTIN_CARD: Dict[str, Any] = {
    "id": "builtin-suwan",
    "name": "苏晚",
    "description": (
        "苏晚是「拾光书斋」的年轻店主，二十多岁，喜欢穿宽松的米色毛衣，"
        "头发随意挽在脑后。她白天修书、晚上写故事，对每个走进书斋的人都很好奇。"
        "她的书斋藏在旧城一条安静的小巷里，门口挂着一枚会响的铜铃。"
    ),
    "personality": (
        "温柔、敏锐、有一点狡黠的幽默感；习惯先听别人说，再用一句轻描淡写的话点破关键。"
        "不太会拒绝人，但对真正在意的事很固执。说话节奏慢，喜欢用比喻。"
    ),
    "scenario": "深夜的拾光书斋，窗外下着细雨，铜铃轻响。你推门而入，苏晚正趴在柜台后改稿。",
    "first_mes": (
        "*铜铃轻响，苏晚从稿纸间抬起头，笔尖还停在半空。*\n\n"
        "「这么晚了，还有人愿意进来避雨呀。」*她把椅子往旁边挪了挪，* "
        "「坐吧。茶刚泡好，虽然有点凉了——你想聊点什么，还是只想安静待一会儿？」"
    ),
    "mes_example": (
        "<START>\n{{user}}: 我只是路过，进来躲躲雨。\n"
        "{{char}}: 「那也很巧。」*她笑了笑，把一只旧茶杯推到你面前，* "
        "「雨天的书斋最适合发呆。放心，我不收你钱。」\n"
        "<START>\n{{user}}: 你在写什么？\n"
        "{{char}}: *她把稿纸往自己那边收了收，又忍不住推回来一半，* "
        "「一个关于迷路的人的故事。不过我还不知道结局——你觉得，他应该找到路，还是留在原地？」"
    ),
    "alternate_greetings": [
        "*你推门时，苏晚正踩在木梯上整理最上层的旧书。她低头看你，眼睛在灯下亮了一下。*\n\n"
        "「来得正好，」*她拍了拍手上的灰，* 「帮我扶一下梯子？作为报酬，你可以随便挑一本书。」"
    ],
    "tags": ["示例", "治愈", "书斋"],
    "creator": "Tavern Lite",
    "character_version": "1.0",
    "source": "builtin",
}

_AVATAR_MAGIC = {
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
    b"RIFF": "image/webp",
}


class RoleplayStore:
    def __init__(self, root: Optional[str] = None):
        default_root = os.path.join(os.path.dirname(NOVELS_DIR), "_roleplay")
        self.root = os.path.abspath(root or os.getenv("ROLEPLAY_DIR") or default_root)
        self.characters_file = os.path.join(self.root, "characters.json")
        self.settings_file = os.path.join(self.root, "settings.json")
        self.chats_dir = os.path.join(self.root, "chats")
        self.history_dir = os.path.join(self.root, "history")
        self.avatars_dir = os.path.join(self.root, "avatars")
        self.worldinfo_dir = os.path.join(self.root, "worldinfo")
        self.personas_dir = os.path.join(self.root, 'personas')
        self._lock = threading.RLock()
        self._generating = set()
        self._characters_signature = None
        self._characters_cache = []
        self._character_index = {}
        os.makedirs(self.chats_dir, exist_ok=True)
        os.makedirs(self.history_dir, exist_ok=True)
        os.makedirs(self.avatars_dir, exist_ok=True)
        os.makedirs(self.worldinfo_dir, exist_ok=True)
        os.makedirs(self.personas_dir, exist_ok=True)
        self._ensure_builtin()

    # ------------------------------------------------------------- characters

    def _refresh_characters(self):
        try:
            info = os.stat(self.characters_file)
            signature = (info.st_mtime_ns, info.st_size)
        except FileNotFoundError:
            signature = (0, 0)
        if signature == self._characters_signature:
            return
        raw = safe_read_json(self.characters_file, default=[])
        if isinstance(raw, dict):
            raw = raw.get('characters') or []
        cards = [self._with_defaults(item) for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
        cards.sort(key=lambda card: (card.get('updated_at') or 0), reverse=True)
        self._characters_cache = cards
        self._character_index = {card['id']: card for card in cards}
        self._characters_signature = signature

    def list_characters(self, *, novel_id=None, summary=False) -> List[Dict[str, Any]]:
        with self._lock:
            self._refresh_characters()
            cards = self._characters_cache
            if novel_id is not None:
                cards = [card for card in cards if card.get('novel_id', '') == novel_id]
            if summary:
                cards = [{key: card.get(key) for key in ('id', 'name', 'novel_id', 'tags', 'source', 'creator', 'updated_at', 'market_source', 'market_reference')}
                         | {'description': str(card.get('description') or '')[:240]} for card in cards]
            return copy.deepcopy(cards)

    def get_character(self, char_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            self._refresh_characters()
            return copy.deepcopy(self._character_index.get(char_id))

    def save_characters(self, cards: List[Dict[str, Any]]) -> None:
        with self._lock:
            atomic_write_json(self.characters_file, [self._with_defaults(c) for c in cards])
            self._characters_signature = None

    def upsert_character(self, card: Dict[str, Any]) -> Dict[str, Any]:
        card = self._with_defaults(card)
        card["updated_at"] = time.time()
        with self._lock:
            cards = self.list_characters()
            for index, existing in enumerate(cards):
                if existing.get("id") == card["id"]:
                    cards[index] = card
                    break
            else:
                cards.insert(0, card)
            self.save_characters(cards)
        return card

    def delete_character(self, char_id: str) -> bool:
        self._validate_character_id(char_id)
        with self._lock:
            cards = self.list_characters()
            kept = [c for c in cards if c.get("id") != char_id]
            if len(kept) == len(cards):
                return False
            self.save_characters(kept)
        for path in (self.chat_path(char_id), self.avatar_path(char_id), self.worldinfo_path(char_id), self.identity_path(char_id)):
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
        return True

    # ----------------------------------------------------------------- avatars

    @staticmethod
    def _validate_character_id(char_id: str) -> str:
        """Keep user-controlled IDs from escaping roleplay storage directories."""
        if not isinstance(char_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", char_id):
            raise ValueError("角色 ID 格式无效")
        return char_id

    def begin_generation(self, char_id: str) -> bool:
        char_id = self._validate_character_id(char_id)
        with self._lock:
            if char_id in self._generating:
                return False
            self._generating.add(char_id)
            return True

    def end_generation(self, char_id: str) -> None:
        char_id = self._validate_character_id(char_id)
        with self._lock:
            self._generating.discard(char_id)

    def is_generating(self, char_id: str) -> bool:
        char_id = self._validate_character_id(char_id)
        with self._lock:
            return char_id in self._generating

    def avatar_path(self, char_id: str) -> str:
        char_id = self._validate_character_id(char_id)
        return os.path.join(self.avatars_dir, f"{char_id}.png")

    def has_avatar(self, char_id: str) -> bool:
        return os.path.isfile(self.avatar_path(char_id))

    def save_avatar(self, char_id: str, data: bytes) -> None:
        with self._lock:
            with open(self.avatar_path(char_id), "wb") as f:
                f.write(data)

    def avatar_media_type(self, char_id: str) -> str:
        try:
            with open(self.avatar_path(char_id), "rb") as f:
                head = f.read(12)
        except OSError:
            return "image/png"
        for magic, media_type in _AVATAR_MAGIC.items():
            if head.startswith(magic):
                return media_type
        return "image/png"

    # ------------------------------------------------------------------- chats

    def chat_path(self, char_id: str) -> str:
        char_id = self._validate_character_id(char_id)
        return os.path.join(self.chats_dir, f"{char_id}.json")

    def archive_chat(self, char_id: str) -> Optional[str]:
        """Preserve the current transcript before a user clears the chat."""
        source = self.chat_path(char_id)
        if not os.path.isfile(source):
            return None
        destination_dir = os.path.join(self.history_dir, char_id)
        os.makedirs(destination_dir, exist_ok=True)
        destination = os.path.join(
            destination_dir, f"{time.time_ns()}-{uuid.uuid4().hex[:8]}.json"
        )
        shutil.copy2(source, destination)
        return destination

    def chat_messages(self, char_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            raw = safe_read_json(self.chat_path(char_id), default={})
        if isinstance(raw, list):
            messages = [m for m in raw if isinstance(m, dict)]
        else:
            messages = raw.get("messages") if isinstance(raw, dict) else None
            messages = [m for m in (messages or []) if isinstance(m, dict)]
        normalized = [self.normalize_message(message) for message in messages]
        # v1 chats had no stable ids; persist generated ids once so clients can
        # deduplicate/update reliably across GETs.
        if any(not str(message.get("id") or "") for message in messages):
            self.save_chat(char_id, normalized)
        return normalized

    def save_chat(self, char_id: str, messages: List[Dict[str, Any]]) -> None:
        now = time.time()
        normalized = [self.normalize_message(message) for message in messages]
        payload = {
            "character_id": char_id,
            "messages": normalized,
            "updated_at": now,
        }
        with self._lock:
            existing = safe_read_json(self.chat_path(char_id), default={})
            created_at = existing.get("created_at") if isinstance(existing, dict) else None
            payload["created_at"] = created_at or now
            atomic_write_json(self.chat_path(char_id), payload)

    @staticmethod
    def normalize_message(message: Dict[str, Any]) -> Dict[str, Any]:
        """Fill v2 message fields (id/kind/speaker/variants) with defaults."""
        if not isinstance(message, dict):
            raise ValueError("消息必须是对象")
        normalized = dict(message)
        normalized.setdefault("id", uuid.uuid4().hex)
        normalized.setdefault("ts", time.time())
        role = str(normalized.get("role") or "user")
        normalized["role"] = role
        kind = str(normalized.get("kind") or "dialogue")
        if kind not in ("dialogue", "action", "narration", "ooc", "roll", "system"):
            kind = "dialogue"
        normalized["kind"] = kind
        normalized.setdefault("speaker", "")
        content = str(normalized.get("content") or "")
        variants = normalized.get("variants")
        if role == "assistant":
            cleaned = [str(item) for item in variants if str(item).strip()] if isinstance(variants, list) else []
            if not cleaned and content.strip():
                cleaned = [content]
            if cleaned:
                try:
                    index = int(normalized.get("variant_index", len(cleaned) - 1))
                except (TypeError, ValueError):
                    index = len(cleaned) - 1
                index = max(0, min(index, len(cleaned) - 1))
                normalized["variants"] = cleaned
                normalized["variant_index"] = index
                normalized["content"] = cleaned[index]
            else:
                normalized.pop("variants", None)
                normalized.pop("variant_index", None)
        else:
            normalized.pop("variants", None)
            normalized.pop("variant_index", None)
            normalized["content"] = content
        return normalized

    # --------------------------------------------------------------- worldinfo

    def worldinfo_path(self, char_id: str) -> str:
        char_id = self._validate_character_id(char_id)
        return os.path.join(self.worldinfo_dir, f"{char_id}.json")

    def load_worldinfo(self, char_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            raw = safe_read_json(self.worldinfo_path(char_id), default=[])
        entries = raw.get("entries") if isinstance(raw, dict) else raw
        if not isinstance(entries, list):
            return []
        normalized: List[Dict[str, Any]] = []
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            try:
                normalized.append(worldinfo.normalize_entry(entry, index))
            except ValueError:
                continue
        return normalized

    def save_worldinfo(self, char_id: str, entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized = [worldinfo.normalize_entry(entry, index) for index, entry in enumerate(entries)]
        with self._lock:
            atomic_write_json(self.worldinfo_path(char_id), {"entries": normalized})
        return normalized

    def merge_worldinfo(self, char_id: str, entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        merged = worldinfo.merge_entries(self.load_worldinfo(char_id), entries)
        return self.save_worldinfo(char_id, merged)

    # ---------------------------------------------------------------- settings

    def identity_path(self, char_id):
        return os.path.join(self.personas_dir, self._validate_character_id(char_id) + '.json')

    def identity(self, char_id):
        with self._lock:
            raw = safe_read_json(self.identity_path(char_id), default={})
        defaults = {**IDENTITY_DEFAULTS, **{k: v for k, v in self.settings().items() if k in IDENTITY_DEFAULTS}}
        defaults.update(raw if isinstance(raw, dict) else {})
        identity = normalize_identity(defaults)
        card = self.get_character(char_id)
        if not raw and card and identity['user_name'].casefold() == str(card['name']).casefold():
            identity.update(user_name='玩家' if card['name'] == '旅行者' else '旅行者', user_persona='')
        return identity

    def update_identity(self, char_id, patch):
        identity = normalize_identity({**self.identity(char_id), **patch})
        card = self.get_character(char_id)
        if card and identity['user_name'].casefold() == str(card['name']).casefold():
            raise ValueError('玩家与 AI 角色不能使用相同名字，请选择另一身份')
        with self._lock:
            atomic_write_json(self.identity_path(char_id), identity)
        return identity

    def effective_settings(self, char_id):
        return {**self.settings(), **self.identity(char_id)}

    def settings(self) -> Dict[str, Any]:
        with self._lock:
            raw = safe_read_json(self.settings_file, default={})
        merged = dict(DEFAULT_SETTINGS)
        if isinstance(raw, dict):
            merged.update({k: v for k, v in raw.items() if k in DEFAULT_SETTINGS})
        return self._clamp_settings(merged)

    def update_settings(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        current = self.settings()
        for key in DEFAULT_SETTINGS:
            if key in patch and patch[key] is not None:
                current[key] = patch[key]
        current = self._clamp_settings(current)
        with self._lock:
            atomic_write_json(self.settings_file, current)
        return current

    # ----------------------------------------------------------------- helpers

    def _clamp_settings(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        clamped = dict(settings)
        try:
            clamped["temperature"] = max(0.0, min(2.0, float(clamped.get("temperature", 0.85))))
        except (TypeError, ValueError):
            clamped["temperature"] = 0.85
        try:
            clamped["max_tokens"] = max(64, min(4096, int(clamped.get("max_tokens", 600))))
        except (TypeError, ValueError):
            clamped["max_tokens"] = 600
        try:
            clamped["context_messages"] = max(4, min(200, int(clamped.get("context_messages", 40))))
        except (TypeError, ValueError):
            clamped["context_messages"] = 40
        clamped["user_name"] = str(clamped.get("user_name") or "旅行者").strip()[:40] or "旅行者"
        for key in ("user_persona", "system_prompt", "authors_note"):
            clamped[key] = str(clamped.get(key) or "")[:8000]
        try:
            clamped["authors_note_depth"] = max(0, min(20, int(clamped.get("authors_note_depth", 4))))
        except (TypeError, ValueError):
            clamped["authors_note_depth"] = 4
        try:
            clamped["lorebook_budget"] = max(1, min(50, int(clamped.get("lorebook_budget", 5))))
        except (TypeError, ValueError):
            clamped["lorebook_budget"] = 5
        try:
            clamped["lorebook_scan_depth"] = max(1, min(50, int(clamped.get("lorebook_scan_depth", 6))))
        except (TypeError, ValueError):
            clamped["lorebook_scan_depth"] = 6
        clamped["lorebook_enabled"] = bool(clamped.get("lorebook_enabled", True))
        return clamped

    def _with_defaults(self, card: Dict[str, Any]) -> Dict[str, Any]:
        merged = copy.deepcopy(_CARD_DEFAULTS)
        merged.update({k: v for k, v in card.items() if v is not None})
        for key in ("alternate_greetings", "tags"):
            if not isinstance(merged.get(key), list):
                merged[key] = []
        return merged

    def _ensure_builtin(self) -> None:
        with self._lock:
            if os.path.exists(self.characters_file):
                return
            card = copy.deepcopy(_BUILTIN_CARD)
            now = time.time()
            card["created_at"] = now
            card["updated_at"] = now
            self.save_characters([card])
