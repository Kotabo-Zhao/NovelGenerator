"""Convert a novel's character bible (plan.json) into SillyTavern-style cards."""
from __future__ import annotations

import time
from typing import Any, Dict, List

from .card_codec import normalize_card


def _flat(value: Any) -> str:
    """Flatten str/dict/list values from the novel plan into readable text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            text = _flat(item)
            if text:
                parts.append(f"{key}：{text}")
        return "；".join(parts)
    if isinstance(value, list):
        return "；".join(_flat(item) for item in value if _flat(item))
    return str(value).strip()


def _scenario_from_plan(plan: Dict[str, Any], title: str) -> str:
    world = plan.get("worldbuilding") or {}
    genre = plan.get("genre") or ""
    parts = [f"《{title}》的世界"]
    if genre:
        parts.append(f"题材：{genre}")
    for key in ("era", "geography", "core_conflict"):
        text = _flat(world.get(key))
        if text:
            parts.append(f"{key}：{text}")
    return "；".join(parts)[:1200]


def build_cards_from_plan(plan: Dict[str, Any], novel_id: str) -> List[Dict[str, Any]]:
    """Build card dicts (without ids/timestamps) from plan characters."""
    if not isinstance(plan, dict):
        return []
    title = str(plan.get("title") or novel_id)
    characters = plan.get("characters") or {}
    if not isinstance(characters, dict):
        return []
    scenario = _scenario_from_plan(plan, title)
    system_prompt = (
        f"你正在扮演小说《{title}》中的角色。请严格保持角色身份、性格与说话方式，"
        "不要提及自己是 AI，不要跳出角色。"
    )
    cards: List[Dict[str, Any]] = []

    protagonist = characters.get("protagonist")
    if isinstance(protagonist, dict) and str(protagonist.get("name") or "").strip():
        name = str(protagonist["name"]).strip()
        description_parts = [
            _flat(protagonist.get("identity")),
            _flat(protagonist.get("appearance")),
            _flat(protagonist.get("backstory")),
        ]
        personality_parts = [
            _flat(protagonist.get("personality")),
            _flat(protagonist.get("motivation")),
            _flat(protagonist.get("catchphrase")),
        ]
        cards.append(_make_card(
            name=name,
            description="\n".join(p for p in description_parts if p),
            personality="；".join(p for p in personality_parts if p),
            scenario=scenario,
            system_prompt=system_prompt,
            tags=["主角", title],
            role="protagonist",
            novel_id=novel_id,
        ))

    for role_key, role_label in (("supporting", "配角"), ("antagonist", "反派")):
        group = characters.get(role_key)
        if not isinstance(group, list):
            continue
        for item in group:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            if role_key == "supporting":
                description = "；".join(p for p in (
                    _flat(item.get("identity")),
                    _flat(item.get("relation")),
                    _flat(item.get("role")),
                    _flat(item.get("mini_arc")),
                    _flat(item.get("meaning")),
                ) if p)
                personality = _flat(item.get("personality"))
            else:
                description = "；".join(p for p in (
                    _flat(item.get("conflict")),
                    _flat(item.get("humanity")),
                ) if p)
                personality = "；".join(p for p in (
                    _flat(item.get("motivation")),
                    _flat(item.get("power")),
                ) if p)
            cards.append(_make_card(
                name=name,
                description=description,
                personality=personality,
                scenario=scenario,
                system_prompt=system_prompt,
                tags=[role_label, title],
                role=role_key,
                novel_id=novel_id,
            ))
    return cards


def _make_card(
    *,
    name: str,
    description: str,
    personality: str,
    scenario: str,
    system_prompt: str,
    tags: List[str],
    role: str,
    novel_id: str,
) -> Dict[str, Any]:
    card = normalize_card({
        "name": name,
        "description": description,
        "personality": personality,
        "scenario": scenario,
        "system_prompt": system_prompt,
        "first_mes": "",
        "mes_example": "",
        "tags": tags,
    })
    card["source"] = "novel"
    card["novel_id"] = novel_id
    card["role_in_novel"] = role
    now = time.time()
    card["created_at"] = now
    card["updated_at"] = now
    return card
