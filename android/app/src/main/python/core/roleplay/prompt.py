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

from .card_codec import apply_macros, macro_context
from .commands import ACTION_DIRECTIVE
from .worldinfo import format_world_info
from .identity import identity_instruction
from .regex_rules import transform

DEFAULT_SYSTEM_PROMPT = (
    "你正在进行沉浸式角色扮演。请遵循角色卡定义的身份、语气、叙事职责和视角回应，"
    "不要擅自更改卡片的角色类型；角色卡可以定义一个人物，也可以定义世界主持或模拟器。"
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
    *, include_post: bool = True,
) -> str:
    char_name = str(card.get("name") or "角色")
    user_name = str(_settings_value(settings, "user_name", "旅行者") or "旅行者")

    default = str(_settings_value(settings, 'system_prompt', '') or '').strip() or DEFAULT_SYSTEM_PROMPT
    base = str(card.get('system_prompt') or '').strip() or default
    context = macro_context(card, settings)
    expand = lambda text: apply_macros(str(text or ''), char_name, user_name, context, original=default)
    before = [e for e in world_entries or [] if e.get('position', 'before_char') == 'before_char']
    after = [e for e in world_entries or [] if e.get('position') == 'after_char']
    parts = [expand(base).strip(), expand(format_world_info(before))]

    description = expand(card.get('description')).strip()
    if description:
        parts.append("[角色描述]\n" + description)

    personality = expand(card.get('personality')).strip()
    if personality:
        parts.append("[性格]\n" + personality)

    scenario = expand(card.get('scenario')).strip()
    if scenario:
        parts.append("[场景]\n" + scenario)

    persona = expand(_settings_value(settings, 'user_persona', '')).strip()
    if persona:
        parts.append(f"[关于 {user_name}]\n" + persona)

    world_text = expand(format_world_info(after))
    if world_text:
        parts.append(world_text)

    post_history = apply_macros(
        str(card.get("post_history_instructions") or ""), char_name, user_name
    ).strip()
    if post_history and include_post:
        parts.append("[补充指示]\n" + post_history)

    parts.append(ACTION_DIRECTIVE)
    parts.append(identity_instruction(card, settings))
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
            line = line.rstrip()
            matched = False
            for name, role in ((char_name, "assistant"), (user_name, "user")):
                for separator in (":", "："):
                    prefix = name + separator
                    if line.lstrip().startswith(prefix):
                        content = line.lstrip()[len(prefix):].strip()
                        if content:
                            current = {"role": role, "content": content}
                            messages.append(current)
                        matched = True
                        break
                if matched:
                    break
            if not matched and current is not None:
                current['content'] += '\n' + line
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
        {"role": "system", "content": build_system_prompt(card, settings, world_entries, include_post=False)}
    ]
    context = macro_context(card, settings, history)
    expand = lambda value: apply_macros(str(value or ''), char_name, user_name, context)
    def world_message(position):
        entries = [e for e in world_entries or [] if e.get('position') == position]
        return expand(format_world_info(entries))
    if world_message('before_examples'):
        messages.append({'role': 'system', 'content': world_message('before_examples')})
    messages.extend(_example_messages(card, char_name, user_name))
    if world_message('after_examples'):
        messages.append({'role': 'system', 'content': world_message('after_examples')})

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

    for index, msg in enumerate(recent):
        role = str(msg.get("role") or "user")
        kind = str(msg.get("kind") or "dialogue")
        content = transform(msg.get('content', ''), card, settings, 1 if role == 'user' else 2,
                            mode='prompt', depth=len(recent)-index-1)
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
    notes = '\n\n'.join(filter(None, [world_message('before_note'), expand(notes), world_message('after_note')]))
    if notes:
        try:
            depth = int(_settings_value(settings, "authors_note_depth", 4))
        except (TypeError, ValueError):
            depth = 4
        depth = max(0, min(20, depth))
        insert_at = max(1, len(messages) - depth)
        messages.insert(insert_at, {
            "role": "system",
            "content": "[作者注释]\n" + apply_macros(notes, char_name, user_name)[:4000],
        })

    # In-chat world info and Character's Note share ST's depth semantics:
    # depth 0 goes at the end of history, depth N before N recent messages.
    injections = []
    for entry in sorted(world_entries or [], key=lambda item: int(item.get('order', 100))):
        if entry.get('position') == 'at_depth':
            injections.append((int(entry.get('depth', 4)), entry.get('role', 'system'),
                               expand(entry.get('content'))))
    depth_prompt = (card.get('extensions') or {}).get('depth_prompt') or {}
    if isinstance(depth_prompt, dict) and depth_prompt.get('prompt'):
        try:
            depth = max(0, min(100, int(depth_prompt.get('depth', 4))))
        except (TypeError, ValueError):
            depth = 4
        role = depth_prompt.get('role', 'system')
        role = {0: 'system', 1: 'user', 2: 'assistant'}.get(role, role) if isinstance(role, (int, str)) else 'system'
        injections.append((depth, role if role in ('system', 'user', 'assistant') else 'system', expand(depth_prompt['prompt'])))
    tail_length = len(messages)
    offset = 0
    for depth, role, text in sorted(injections, key=lambda item: -item[0]):
        if text:
            messages.insert(max(1, tail_length - depth) + offset, {'role': role, 'content': text})
            offset += 1

    post = expand(card.get('post_history_instructions')).strip()
    if post:
        messages.append({'role': 'system', 'content': '[补充指示]\n' + post})

    if directive:
        messages.append({"role": "system", "content": expand(directive)})

    # Some providers reject a trailing assistant message (e.g. after regenerate
    # trimming); the UI guarantees a user message exists, but be defensive.
    if messages and messages[-1].get("role") == "assistant" and not directive:
        messages.append({"role": "user", "content": "（继续）"})
    return messages
