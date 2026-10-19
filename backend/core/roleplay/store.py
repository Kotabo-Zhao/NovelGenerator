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
import math
import os
import re
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from config import NOVELS_DIR
from ..atomic_io import atomic_write_json, safe_read_json
from ..llm_text import display_text

DEFAULT_SETTINGS: Dict[str, Any] = {
    "user_name": "旅行者",
    "user_persona": "",
    "system_prompt": "",
    "temperature": 0.85,
    "max_tokens": 600,
    "context_messages": 40,
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
        self.avatars_dir = os.path.join(self.root, "avatars")
        self._lock = threading.RLock()
        self._generating = set()
        self._mutating = set()
        self._cards_cache = None
        self._cards_stamp = None
        os.makedirs(self.chats_dir, exist_ok=True)
        os.makedirs(self.avatars_dir, exist_ok=True)
        self._ensure_builtin()

    # ------------------------------------------------------------- characters

    def list_characters(self) -> List[Dict[str, Any]]:
        with self._lock:
            try:
                stat = os.stat(self.characters_file)
                stamp = (stat.st_mtime_ns, stat.st_size)
            except FileNotFoundError:
                stamp = None
            if self._cards_cache is not None and stamp == self._cards_stamp:
                return copy.deepcopy(self._cards_cache)
            raw = safe_read_json(self.characters_file, default=[])
        if isinstance(raw, dict):
            raw = raw.get("characters") or []
        if not isinstance(raw, list):
            raw = []
        cards = [self._with_defaults(item) for item in raw if isinstance(item, dict)]
        cards.sort(key=lambda c: (c.get("updated_at") or 0), reverse=True)
        with self._lock:
            self._cards_cache = cards
            self._cards_stamp = stamp
        return copy.deepcopy(cards)

    def get_character(self, char_id: str) -> Optional[Dict[str, Any]]:
        for card in self.list_characters():
            if card.get("id") == char_id:
                return card
        return None

    def save_characters(self, cards: List[Dict[str, Any]]) -> None:
        with self._lock:
            atomic_write_json(self.characters_file, [self._with_defaults(c) for c in cards])
            self._cards_cache = None

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
        with self._lock:
            cards = self.list_characters()
            kept = [c for c in cards if c.get("id") != char_id]
            if len(kept) == len(cards):
                return False
            self.save_characters(kept)
        for path in (self.chat_path(char_id), self.avatar_path(char_id)):
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
        return True

    # ----------------------------------------------------------------- avatars

    def avatar_path(self, char_id: str) -> str:
        self.validate_id(char_id)
        return os.path.join(self.avatars_dir, f"{char_id}.png")

    def has_avatar(self, char_id: str) -> bool:
        return os.path.isfile(self.avatar_path(char_id))

    def save_avatar(self, char_id: str, data: bytes) -> None:
        with self._lock:
            path = self.avatar_path(char_id)
            temporary = path + "." + uuid.uuid4().hex + ".tmp"
            try:
                with open(temporary, "wb") as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.remove(temporary)

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
        self.validate_id(char_id)
        return os.path.join(self.chats_dir, f"{char_id}.json")

    def chat_messages(self, char_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            raw = safe_read_json(self.chat_path(char_id), default={})
        messages = raw if isinstance(raw, list) else raw.get("messages", []) if isinstance(raw, dict) else []
        if not isinstance(messages, list):
            return []
        result = []
        for index, item in enumerate(messages or []):
            if not isinstance(item, dict) or item.get("role") not in ("user", "assistant"):
                continue
            message = dict(item)
            try:
                message["content"] = display_text(message.get("content"))
            except TypeError:
                message["content"] = "（这条旧消息的格式无法显示）"
            message.setdefault("id", uuid.uuid5(uuid.NAMESPACE_URL, f"{char_id}/{index}/{message.get('ts', 0)}").hex)
            result.append(message)
        return result

    def save_chat(self, char_id: str, messages: List[Dict[str, Any]]) -> None:
        now = time.time()
        payload = {
            "character_id": char_id,
            "messages": messages,
            "updated_at": now,
        }
        with self._lock:
            existing = safe_read_json(self.chat_path(char_id), default={})
            created_at = existing.get("created_at") if isinstance(existing, dict) else None
            payload["created_at"] = created_at or now
            atomic_write_json(self.chat_path(char_id), payload)

    # ---------------------------------------------------------------- settings

    def settings(self) -> Dict[str, Any]:
        with self._lock:
            raw = safe_read_json(self.settings_file, default={})
        merged = dict(DEFAULT_SETTINGS)
        if isinstance(raw, dict):
            merged.update({k: v for k, v in raw.items() if k in DEFAULT_SETTINGS})
        return self._clamp_settings(merged)

    def update_settings(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            current = self.settings()
            for key in DEFAULT_SETTINGS:
                if key in patch and patch[key] is not None:
                    current[key] = patch[key]
            current = self._clamp_settings(current)
            atomic_write_json(self.settings_file, current)
        return current

    @staticmethod
    def validate_id(char_id: str) -> None:
        if not isinstance(char_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", char_id):
            raise ValueError("无效的角色 ID")

    def is_generating(self, char_id: str) -> bool:
        with self._lock:
            return char_id in self._generating

    def begin_generation(self, char_id: str) -> bool:
        with self._lock:
            if char_id in self._generating or char_id in self._mutating:
                return False
            self._generating.add(char_id)
            return True

    def end_generation(self, char_id: str) -> None:
        with self._lock:
            self._generating.discard(char_id)

    def begin_mutation(self, char_id: str) -> bool:
        with self._lock:
            if char_id in self._generating or char_id in self._mutating:
                return False
            self._mutating.add(char_id)
            return True

    def end_mutation(self, char_id: str) -> None:
        with self._lock:
            self._mutating.discard(char_id)

    def archive_chat(self, char_id: str) -> None:
        """Keep recoverable snapshots before a user clears or edits a transcript."""
        messages = self.chat_messages(char_id)
        if not messages:
            return
        folder = os.path.join(self.root, "history", char_id)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{time.time_ns()}.json")
        atomic_write_json(path, {"messages": messages, "character_id": char_id})
        snapshots = sorted(os.listdir(folder))
        for old in snapshots[:-30]:
            os.remove(os.path.join(folder, old))

    # ----------------------------------------------------------------- helpers

    def _clamp_settings(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        clamped = dict(settings)
        try:
            temperature = float(clamped.get("temperature", 0.85))
            clamped["temperature"] = max(0.0, min(2.0, temperature)) if math.isfinite(temperature) else 0.85
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
        for key in ("user_persona", "system_prompt"):
            clamped[key] = str(clamped.get(key) or "")[:8000]
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
