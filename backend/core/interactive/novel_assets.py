"""Novel asset collection for interactive/group-scene mode.

Collects everything the novel has produced so it can be inspected/exported and
re-synced into an interactive save:

* plan.json / outline / chapters
* bible.json (manual world book: characters, locations, terms, relations)
* character_bible.json, character_profiles.json, character_voices.json
* matching roleplay character cards (description/personality/avatar)
* current interactive state (casts, world, facts, relations, blocks)
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from config import NOVELS_DIR
from core.path_security import safe_novel_dir

_MAX_CHAPTER_CHARS = 200_000


def _read_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _chapter_files(novel_dir: str) -> List[Dict[str, Any]]:
    chapters_dir = os.path.join(novel_dir, "chapters")
    if not os.path.isdir(chapters_dir):
        return []
    items: List[Dict[str, Any]] = []
    for name in sorted(os.listdir(chapters_dir)):
        if not name.endswith((".md", ".txt")):
            continue
        path = os.path.join(chapters_dir, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read(_MAX_CHAPTER_CHARS)
        except Exception:
            continue
        items.append({"file": name, "chars": len(content), "content": content})
    return items


def _roleplay_cards(novel_id: str) -> Dict[str, Dict[str, Any]]:
    """Cards keyed by name (never raises when roleplay mode is unused)."""
    try:
        from core.roleplay.store import RoleplayStore
        store = RoleplayStore()
        cards = [card for card in store.list_characters() if card.get('novel_id', '') in ('', novel_id)]
        cards.sort(key=lambda card: (card.get('novel_id') == novel_id, card.get('updated_at') or 0), reverse=True)
    except Exception:
        return {}
    result: Dict[str, Dict[str, Any]] = {}
    for card in cards:
        name = str(card.get("name") or "").strip()
        if not name or name in result:
            continue
        cid = str(card.get("id") or "")
        result[name] = {
            "id": cid,
            "name": name,
            "description": card.get("description", ""),
            "personality": card.get("personality", ""),
            "scenario": card.get("scenario", ""),
            "first_mes": card.get("first_mes", ""),
            "tags": card.get("tags", []),
            "source": card.get("source", ""),
            "avatar_url": (
                f"/api/roleplay/characters/{cid}/avatar"
                if cid and store.has_avatar(cid) else ""
            ),
            "has_card": True,
        }
    return result


def worldbook_brief(bible: Dict[str, Any], plan: Dict[str, Any]) -> str:
    """Compact text summary of the world book for prompt injection."""
    parts: List[str] = []
    wb = plan.get("worldbuilding") or {}
    for key in ("era", "geography", "power_system", "core_conflict", "factions"):
        value = wb.get(key)
        if value:
            parts.append(f"{key}: {json.dumps(value, ensure_ascii=False)[:180] if not isinstance(value, str) else value[:180]}")
    if isinstance(bible, dict):
        for name, loc in list((bible.get("locations") or {}).items())[:20]:
            if not isinstance(loc, dict):
                continue
            desc = str(loc.get("description") or loc.get("note") or "").strip()
            if desc:
                parts.append(f"地点·{name}: {desc[:180]}")
        for name, term in list((bible.get("terms") or {}).items())[:20]:
            if not isinstance(term, dict):
                continue
            desc = str(term.get("description") or term.get("note") or "").strip()
            if desc:
                parts.append(f"设定·{name}: {desc[:180]}")
        for relation in (bible.get("relations") or [])[:20]:
            if isinstance(relation, dict):
                parts.append(
                    "关系: " + str(relation.get("from") or relation.get("a") or "")
                    + " → " + str(relation.get("to") or relation.get("b") or "")
                    + "：" + str(relation.get("relation") or relation.get("desc") or "")[:100]
                )
            elif str(relation).strip():
                parts.append("关系: " + str(relation)[:160])
    return "\n".join(part for part in parts if part)[:2400]


def load_novel_assets(novel_id: str, *, include_chapters: bool = True) -> Dict[str, Any]:
    novel_dir = safe_novel_dir(NOVELS_DIR, novel_id)
    plan = _read_json(os.path.join(novel_dir, "plan.json"), {})
    bible = _read_json(os.path.join(novel_dir, "bible.json"), {})
    character_bible = _read_json(os.path.join(novel_dir, "character_bible.json"), {})
    profiles = _read_json(os.path.join(novel_dir, "character_profiles.json"), {})
    voices = _read_json(os.path.join(novel_dir, "character_voices.json"), {})
    cards = _roleplay_cards(novel_id)
    cast_names = set()
    chars = plan.get("characters") or {}
    for role_key in ("protagonist", "supporting", "antagonist"):
        group = chars.get(role_key)
        items = [group] if isinstance(group, dict) else (group or [])
        for item in items:
            if isinstance(item, dict) and item.get("name"):
                cast_names.add(str(item["name"]))
    for group_key in ("protagonist", "supporting", "antagonist"):
        group = character_bible.get(group_key)
        items = [group] if isinstance(group, dict) else (group or [])
        for item in items:
            if isinstance(item, dict) and item.get("name"):
                cast_names.add(str(item["name"]))
    card_subset = {name: cards[name] for name in cast_names if name in cards}
    return {
        "novel_id": novel_id,
        "plan": plan,
        "bible": bible,
        "character_bible": character_bible,
        "character_profiles": profiles,
        "character_voices": voices,
        "roleplay_cards": card_subset,
        "cast_names": sorted(cast_names),
        "chapters": _chapter_files(novel_dir) if include_chapters else [],
        "worldbook_brief": worldbook_brief(bible, plan),
    }


def sync_assets_to_state(state: Dict[str, Any], assets: Dict[str, Any]) -> Dict[str, int]:
    """Merge novel assets into an interactive state (casts/worldbook)."""
    if not isinstance(state, dict):
        raise ValueError("互动存档不存在")
    plan = assets.get("plan") or {}
    bible = assets.get("bible") or {}
    character_bible = assets.get("character_bible") or {}
    profiles = assets.get("character_profiles") or {}
    voices = assets.get("character_voices") or {}
    cards = assets.get("roleplay_cards") or {}

    casts = state.setdefault("casts", {})
    player = str((state.get("player_char") or {}).get("name") or "")
    plan_chars: Dict[str, Dict[str, Any]] = {}
    for role_key in ("protagonist", "supporting", "antagonist"):
        group = (plan.get("characters") or {}).get(role_key)
        items = [group] if isinstance(group, dict) else (group or [])
        for item in items:
            if isinstance(item, dict) and item.get("name"):
                plan_chars[str(item["name"])] = item
    added = 0
    updated = 0
    for name in assets.get("cast_names", []):
        if not name or name == player:
            continue
        entry = casts.setdefault(name, {})
        if not entry:
            added += 1
        before = json.dumps(entry, ensure_ascii=False, sort_keys=True)
        for source in (plan_chars, character_bible):
            group = source.get(name)
            if isinstance(group, dict):
                for key in ("identity", "personality", "role", "speak_style"):
                    if group.get(key) and not entry.get(key):
                        entry[key] = group[key]
        if isinstance(profiles.get(name), dict):
            entry["profile"] = profiles[name]
        if isinstance(voices.get(name), dict):
            entry["voice"] = voices[name]
        if isinstance(cards.get(name), dict):
            entry["card"] = {
                "description": cards[name].get("description", ""),
                "personality": cards[name].get("personality", ""),
                "avatar_url": cards[name].get("avatar_url", ""),
            }
        if json.dumps(entry, ensure_ascii=False, sort_keys=True) != before:
            updated += 1

    brief = assets.get("worldbook_brief") or ""
    if brief:
        existing = str(state.get("worldbuilding_brief") or "")
        if brief not in existing:
            state["worldbuilding_brief"] = (existing + "\n[世界书]\n" + brief)[:3000]
    state["assets_synced_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return {"cast_added": added, "cast_updated": updated,
            "profiles": len(profiles), "voices": len(voices), "cards": len(cards)}
