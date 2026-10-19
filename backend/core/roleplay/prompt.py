"""Roleplay prompt assembly — SillyTavern-flavoured.

A request is built as:

    [system: base prompt + description/personality/scenario/user persona]
    [example dialogue messages parsed from mes_example]
    [recent chat history]
    [post-history instructions appended to the system prompt]

``{{char}}`` / ``{{user}}`` macros (and ``<BOT>`` / ``<USER>``) are expanded in
every card field before it reaches the model.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from .card_codec import apply_macros
from .commands import ACTION_DIRECTIVE
from .worldinfo import format_world_info

DEFAULT_SYSTEM_PROMPT = (
    "你正在进行沉浸式角色扮演。请始终以角色的身份、语气和视角回应，"
    "不要以 AI 或助手的身份说话，不要解释规则，不要跳出角色。"
    "可以用 *斜体* 描写动作，用「引号」说出台词。回复自然、有画面感，长度适中。"
)

_MAX_EXAMPLE_CHARS = 6000
_MAX_CONTEXT_CHARS = 24000
_MAX_SINGLE_MESSAGE_CHARS = 8000


def _settings_value(settings: Dict[str, Any], key: str, default):
    value = settings.get(key, default) if isinstance(settings, dict) else default
    return default if value is None else value


def build_system_prompt(
    card: Dict[str, Any],
    settings: Dict[str, Any],
    world_entries: List[Dict[str, Any]] = None,
) -> str:
    char_name = str(card.get("name") or "角色")
    user_name = str(_settings_value(settings, "user_name", "旅行者") or "旅行者")

    base = (
        str(card.get("system_prompt") or "").strip()
        or str(_settings_value(settings, "system_prompt", "") or "").strip()
        or DEFAULT_SYSTEM_PROMPT
    )
    parts = [apply_macros(base, char_name, user_name).strip()]

    description = apply_macros(str(card.get("description") or ""), char_name, user_name).strip()
    if description:
        parts.append("[角色描述]\n" + description)

    personality = apply_macros(str(card.get("personality") or ""), char_name, user_name).strip()
    if personality:
        parts.append("[性格]\n" + personality)

    scenario = apply_macros(str(card.get("scenario") or ""), char_name, user_name).strip()
    if scenario:
        parts.append("[场景]\n" + scenario)

    persona = apply_macros(str(_settings_value(settings, "user_persona", "") or ""), char_name, user_name).strip()
    if persona:
        parts.append(f"[关于 {user_name}]\n" + persona)

    world_text = format_world_info(world_entries or [])
    if world_text:
        parts.append(world_text)

    post_history = apply_macros(
        str(card.get("post_history_instructions") or ""), char_name, user_name
    ).strip()
    if post_history:
        parts.append("[补充指示]\n" + post_history)

    parts.append(ACTION_DIRECTIVE)
    parts.append("请保持角色扮演，不要脱离角色。")
    return "\n\n".join(part for part in parts if part)


def _example_messages(card: Dict[str, Any], char_name: str, user_name: str) -> List[Dict[str, str]]:
    """Parse ST ``mes_example`` blocks (``<START>`` separated name: text lines)."""
    raw = apply_macros(str(card.get("mes_example") or ""), char_name, user_name)
    if not raw.strip():
        return []
    raw = raw[:_MAX_EXAMPLE_CHARS]
    messages: List[Dict[str, str]] = []
    for block in re.split(r"<START>", raw, flags=re.IGNORECASE):
        for line in block.splitlines():
            line = line.strip()
            if not line:
                continue
            matched = False
            for name, role in ((char_name, "assistant"), (user_name, "user")):
                for separator in (":", "："):
                    prefix = name + separator
                    if line.startswith(prefix):
                        content = line[len(prefix):].strip()
                        if content:
                            messages.append({"role": role, "content": content})
                        matched = True
                        break
                if matched:
                    break
    return messages


def build_messages(
    card: Dict[str, Any],
    settings: Dict[str, Any],
    history: List[Dict[str, Any]],
    world_entries: List[Dict[str, Any]] = None,
    directive: str = "",
) -> List[Dict[str, str]]:
    """Build the OpenAI-style message list for one generation call.

    ``world_entries`` are pre-activated lorebook entries. ``directive`` is an
    extra system instruction used by commands (/continue, /impersonate, swipe).
    """
    char_name = str(card.get("name") or "角色")
    user_name = str(_settings_value(settings, "user_name", "旅行者") or "旅行者")

    messages: List[Dict[str, str]] = [
        {"role": "system", "content": build_system_prompt(card, settings, world_entries)}
    ]
    messages.extend(_example_messages(card, char_name, user_name))

    context_messages = int(_settings_value(settings, "context_messages", 40) or 40)
    context_messages = max(4, min(200, context_messages))
    recent = [
        msg for msg in history
        if isinstance(msg, dict)
        and msg.get("role") in ("user", "assistant", "system")
        and str(msg.get("content") or "").strip()
    ][-context_messages:]

    # Character/token budget: keep the newest messages that fit, so very long
    # chats or oversized single messages cannot overflow the provider context.
    trimmed: List[Dict[str, Any]] = []
    used_chars = 0
    for msg in reversed(recent):
        content = str(msg.get("content") or "")
        if len(content) > _MAX_SINGLE_MESSAGE_CHARS:
            content = content[:_MAX_SINGLE_MESSAGE_CHARS]
        if trimmed and used_chars + len(content) > _MAX_CONTEXT_CHARS:
            break
        trimmed.append({**msg, "content": content})
        used_chars += len(content)
    recent = list(reversed(trimmed))

    for msg in recent:
        role = str(msg.get("role") or "user")
        kind = str(msg.get("kind") or "dialogue")
        content = str(msg.get("content") or "")
        if kind == "action":
            messages.append({"role": "user", "content": "[动作] " + content})
        elif kind == "narration":
            messages.append({"role": "assistant", "content": "【旁白】" + content})
        elif kind == "ooc":
            messages.append({"role": "system", "content": "[OOC] " + content})
        elif kind == "roll":
            messages.append({"role": "system", "content": "[骰子] " + content})
        else:
            messages.append({"role": role, "content": content})

    # Author's note at configurable depth (ST-like), keeping the tail intact.
    notes = str(_settings_value(settings, "authors_note", "") or "").strip()
    if notes:
        try:
            depth = int(_settings_value(settings, "authors_note_depth", 4) or 4)
        except (TypeError, ValueError):
            depth = 4
        depth = max(0, min(20, depth))
        insert_at = max(1, len(messages) - depth)
        messages.insert(insert_at, {
            "role": "system",
            "content": "[作者注释]\n" + apply_macros(notes, char_name, user_name)[:4000],
        })

    if directive:
        messages.append({"role": "system", "content": directive})

    # Some providers reject a trailing assistant message (e.g. after regenerate
    # trimming); the UI guarantees a user message exists, but be defensive.
    if messages and messages[-1].get("role") == "assistant" and not directive:
        messages.append({"role": "user", "content": "（继续）"})
    return messages
