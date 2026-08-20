"""Versioned chapter contracts shared by planning, writing and UI layers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


CHAPTER_CONTRACT_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _positive_int(value: Any, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        values = [f"{key}: {item}" for key, item in value.items()]
    elif isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        values = [value]
    result = []
    for item in values:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _first_nonempty(*values: Any) -> list[str]:
    result = []
    for value in values:
        for text in _text_list(value):
            if text not in result:
                result.append(text)
    return result


def _normalize_scene_beats(value: Any) -> list[dict]:
    beats = []
    raw_beats = value if isinstance(value, (list, tuple)) else []
    for index, raw in enumerate(raw_beats, 1):
        if isinstance(raw, dict):
            beat = dict(raw)
            action = str(
                beat.get("key_action")
                or beat.get("action")
                or beat.get("event")
                or beat.get("summary")
                or ""
            ).strip()
            beat["beat"] = _positive_int(beat.get("beat"), index)
            beat["name"] = str(beat.get("name") or f"节拍{index}").strip()
            beat["key_action"] = action
        else:
            action = str(raw or "").strip()
            if not action:
                continue
            beat = {"beat": index, "name": f"节拍{index}", "key_action": action}
        beats.append(beat)
    return beats


def normalize_chapter_contract(chapter: dict | None) -> dict:
    """Return one chapter with a stable additive contract.

    Legacy outline fields remain untouched.  Missing contract fields are derived
    only from facts already present in the chapter; no new story event is invented.
    """
    result = dict(chapter or {})
    try:
        current_version = int(result.get("chapter_contract_version") or 0)
    except (TypeError, ValueError):
        current_version = 0

    beats = _normalize_scene_beats(result.get("scene_beats"))
    actions = [beat["key_action"] for beat in beats if beat.get("key_action")]

    result["chapter_contract_version"] = max(current_version, CHAPTER_CONTRACT_VERSION)
    result["before_state"] = _text_list(result.get("before_state")) or _first_nonempty(
        result.get("cause_from_prev"), result.get("opening_scene")
    )
    result["scene_beats"] = beats
    result["must_happen"] = _text_list(result.get("must_happen")) or actions or _first_nonempty(
        result.get("summary"), result.get("conflict")
    )
    result["after_state"] = _text_list(result.get("after_state")) or _text_list(
        result.get("bridge_to_next")
    )
    result["new_information"] = _text_list(result.get("new_information"))
    result["open_loop"] = _text_list(result.get("open_loop")) or _text_list(result.get("hook"))
    result["forbidden_repeats"] = _text_list(result.get("forbidden_repeats"))
    result["requirement_ids"] = _text_list(result.get("requirement_ids"))

    concrete_actions = [action for action in result["must_happen"] if len(action) >= 8]
    result["contract_status"] = "ready" if len(concrete_actions) >= 3 else "needs_expansion"
    return result


def migrate_plan_chapter_contracts(plan: dict | None) -> dict:
    """Upgrade every chapter in-place and return a deterministic migration report."""
    if not isinstance(plan, dict):
        return {
            "schema_version": CHAPTER_CONTRACT_VERSION,
            "chapter_count": 0,
            "migrated_count": 0,
            "ready_count": 0,
            "needs_expansion_count": 0,
        }

    chapters = []
    for volume in ((plan.get("outline") or {}).get("volumes") or []):
        if not isinstance(volume, dict):
            continue
        for chapter in volume.get("chapters") or []:
            if isinstance(chapter, dict):
                chapters.append(chapter)

    migrated_count = 0
    for chapter in chapters:
        normalized = normalize_chapter_contract(chapter)
        if normalized != chapter:
            chapter.clear()
            chapter.update(normalized)
            migrated_count += 1

    ready_count = sum(chapter.get("contract_status") == "ready" for chapter in chapters)
    needs_expansion_count = len(chapters) - ready_count
    meta = plan.setdefault("_meta", {}).setdefault("chapter_contract", {})
    meta_changed = (
        _positive_int(meta.get("schema_version"), 0) < CHAPTER_CONTRACT_VERSION
        or meta.get("chapter_count") != len(chapters)
        or meta.get("ready_count") != ready_count
        or meta.get("needs_expansion_count") != needs_expansion_count
    )
    meta.update({
        "schema_version": CHAPTER_CONTRACT_VERSION,
        "chapter_count": len(chapters),
        "ready_count": ready_count,
        "needs_expansion_count": needs_expansion_count,
    })
    if migrated_count or meta_changed:
        meta["updated_at"] = _now()

    return {
        "schema_version": CHAPTER_CONTRACT_VERSION,
        "chapter_count": len(chapters),
        "migrated_count": migrated_count,
        "ready_count": ready_count,
        "needs_expansion_count": needs_expansion_count,
    }
