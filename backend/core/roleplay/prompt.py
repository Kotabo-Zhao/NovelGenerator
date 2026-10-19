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

DEFAULT_SYSTEM_PROMPT = (
    "你正在进行沉浸式角色扮演。请始终以角色的身份、语气和视角回应，"
    "不要以 AI 或助手的身份说话，不要解释规则，不要跳出角色。"
    "可以用 *斜体* 描写动作，用「引号」说出台词。回复自然、有画面感，长度适中。"
)

_MAX_EXAMPLE_CHARS = 6000


def _settings_value(settings: Dict[str, Any], key: str, default):
    value = settings.get(key, default) if isinstance(settings, dict) else default
    return default if value is None else value


def build_system_prompt(card: Dict[str, Any], settings: Dict[str, Any]) -> str:
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

    post_history = apply_macros(
        str(card.get("post_history_instructions") or ""), char_name, user_name
    ).strip()
    if post_history:
        parts.append("[补充指示]\n" + post_history)

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
        current = None
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
                            current = {"role": role, "content": content}
                            messages.append(current)
                        matched = True
                        break
                if matched:
                    break
            if not matched and current is not None:
                current["content"] += "\n" + line
    return messages


def build_messages(
    card: Dict[str, Any],
    settings: Dict[str, Any],
    history: List[Dict[str, Any]],
) -> List[Dict[str, str]]:
    """Build the OpenAI-style message list for one generation call."""
    char_name = str(card.get("name") or "角色")
    user_name = str(_settings_value(settings, "user_name", "旅行者") or "旅行者")

    messages: List[Dict[str, str]] = [
        {"role": "system", "content": build_system_prompt(card, settings)}
    ]
    messages.extend(_example_messages(card, char_name, user_name))

    context_messages = int(_settings_value(settings, "context_messages", 40) or 40)
    context_messages = max(4, min(200, context_messages))
    recent = [
        msg for msg in history
        if isinstance(msg, dict)
        and msg.get("role") in ("user", "assistant")
        and str(msg.get("content") or "").strip()
    ][-context_messages:]
    # A card can request 200 very large messages. Keep the newest complete
    # messages within a character budget instead of overflowing the provider.
    budget = 48000
    selected = []
    for message in reversed(recent):
        content = str(message["content"])
        if selected and len(content) > budget:
            break
        selected.append({"role": message["role"], "content": content[-budget:]})
        budget -= len(selected[-1]["content"])
        if budget <= 0:
            break
    messages.extend(reversed(selected))

    # Some providers reject a trailing assistant message (e.g. after regenerate
    # trimming); the UI guarantees a user message exists, but be defensive.
    if messages[-1]["role"] == "assistant":
        messages.append({"role": "user", "content": "（继续）"})
    return messages
