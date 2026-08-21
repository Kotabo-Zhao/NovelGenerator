"""Shared contract for carrying creative requirements through planning and writing.

The contract is deliberately deterministic.  LLM supervision can add judgement, but
chapter ownership, prompt injection and the basic evidence report must remain stable
between workers and across retries.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable


SCHEMA_VERSION = 2
REQUIREMENT_KINDS = (
    "book_invariant",
    "milestone",
    "chapter_event",
    "planning_only",
    "preference",
)

_PLANNING_HINTS = (
    "volume_plan",
    "volume_planning",
    "chapter_plan",
    "chapter_planning",
    "three_act",
    "three_acts",
    "structure_plan",
)
_MILESTONE_HINTS = (
    "turning_point",
    "turning_points",
    "climax",
    "ending",
    "opening_hook",
    "hook",
    "suspense",
    "foreshadow",
    "subplot",
    "relationship_arc",
    "growth_arc",
)
_INVARIANT_HINTS = (
    "perspective",
    "point_of_view",
    "pov",
    "setting",
    "special_rule",
    "special_rules",
    "power_system",
    "social_structure",
    "protagonist",
    "supporting",
    "antagonist",
    "main_plot",
    "main_story",
    "main_storyline",
    "core_conflict",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [item for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _chapter_numbers(value: Any) -> list[int]:
    result = []
    for raw in _as_list(value):
        number = _safe_int(raw)
        if number > 0:
            result.append(number)
    return sorted(set(result))


def infer_requirement_kind(requirement: dict | None) -> str:
    """Infer a stable v2 kind for legacy requirement records.

    Classification is deliberately based on existing structured fields before
    free-form prose.  This keeps old works deterministic across reloads and avoids
    reclassifying them when wording changes.
    """
    item = requirement or {}
    explicit = str(item.get("kind") or "").strip().lower()
    if explicit in REQUIREMENT_KINDS:
        return explicit

    category = str(item.get("category") or "").strip().lower()
    sub_category = str(item.get("sub_category") or "").strip().lower()
    scope = str(item.get("scope") or "global").strip().lower()
    signature = f"{category} {sub_category}"

    if category == "style":
        return "preference"
    if any(hint in signature for hint in _PLANNING_HINTS):
        return "planning_only"
    if any(hint in signature for hint in _MILESTONE_HINTS):
        return "milestone"
    if scope == "chapter":
        return "chapter_event"
    if _chapter_numbers(item.get("chapter_numbers")) and not category:
        return "chapter_event"
    if scope in {"opening", "mid", "ending", "distributed"}:
        return "milestone"
    if category in {"worldbuilding", "character"}:
        return "book_invariant"
    if any(hint in signature for hint in _INVARIANT_HINTS):
        return "book_invariant"
    if category == "structure":
        return "planning_only"
    if category == "plot":
        return "milestone"
    return "book_invariant"


def _chapter_items(plan: dict) -> list[dict]:
    outline = (plan or {}).get("outline") or {}
    items: list[dict] = []
    for volume in outline.get("volumes") or []:
        if not isinstance(volume, dict):
            continue
        for chapter in volume.get("chapters") or []:
            if isinstance(chapter, dict):
                items.append(chapter)
    return items


def _chapter_number(chapter: dict, fallback: int) -> int:
    for key in ("chapter_num", "number", "chapter_number"):
        try:
            value = int(chapter.get(key))
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass
    return fallback


def _total_chapters(plan: dict, chapters: list[dict]) -> int:
    try:
        value = int(((plan or {}).get("outline") or {}).get("total_chapters") or 0)
        if value > 0:
            return value
    except (TypeError, ValueError):
        pass
    return max((_chapter_number(chapter, index) for index, chapter in enumerate(chapters, 1)), default=0)


def normalize_requirements(requirements: dict | None, *, novel_id: str = "") -> dict:
    """Return a backward-compatible v2 snapshot with stable IDs and kinds."""
    source = dict(requirements or {})
    source_schema = _safe_int(source.get("schema_version"))
    subtasks = []
    for index, raw in enumerate(source.get("subtasks") or [], 1):
        item = dict(raw or {})
        item["id"] = str(item.get("id") or f"R{index:03d}")
        item["title"] = str(item.get("title") or "Unnamed requirement").strip()
        item["description"] = str(item.get("description") or "").strip()
        item["priority"] = str(item.get("priority") or "P1").upper()
        item["scope"] = str(item.get("scope") or "global").lower()
        item["kind"] = infer_requirement_kind(item)
        item["must_include"] = _as_list(item.get("must_include"))
        item["must_avoid"] = _as_list(item.get("must_avoid"))
        item["quality_criteria"] = _as_list(item.get("quality_criteria"))
        item["generation_hint"] = str(item.get("generation_hint") or "").strip()
        item["status"] = str(item.get("status") or "pending")
        owned = _chapter_numbers(item.get("chapter_numbers"))
        if item["kind"] in {"book_invariant", "planning_only", "preference"}:
            owned = []
        elif (
            item["kind"] == "milestone"
            and source_schema < SCHEMA_VERSION
            and not raw.get("kind")
            and item["scope"] == "global"
        ):
            # v1 expanded every global item to every chapter.  Those synthetic
            # assignments are not meaningful milestone ownership.
            owned = []
        item["chapter_numbers"] = owned
        subtasks.append(item)

    source["schema_version"] = max(_safe_int(source.get("schema_version")), SCHEMA_VERSION)
    source["version"] = max(_safe_int(source.get("version")), 1)
    source["updated_at"] = source.get("updated_at") or _now()
    if novel_id:
        source["novel_id"] = novel_id
    source["subtasks"] = subtasks
    source["total_count"] = len(subtasks)
    source["p0_count"] = sum(item["priority"] == "P0" for item in subtasks)
    source["kind_counts"] = {
        kind: sum(item["kind"] == kind for item in subtasks)
        for kind in REQUIREMENT_KINDS
    }
    return source


def _scope_chapters(scope: str, chapter_numbers: list[int], total: int) -> list[int]:
    if chapter_numbers:
        return sorted(set(chapter_numbers))
    if not total:
        return []
    if scope == "opening":
        return list(range(1, max(2, total // 10) + 1))
    if scope == "ending":
        start = max(1, total - max(1, total // 10) + 1)
        return list(range(start, total + 1))
    if scope == "mid":
        start = max(1, total // 3)
        end = max(start, (total * 2) // 3)
        return list(range(start, end + 1))
    if scope == "global":
        # v2.8.5: global scope 的 milestone（如"每5章一个反转"）此前返回 [] 永不激活，
        # 被静默排除在章节指令之外。global 意味着贯穿全书。
        return list(range(1, total + 1))
    return []


def _owned_chapters(item: dict, total: int) -> list[int]:
    kind = item.get("kind") or infer_requirement_kind(item)
    explicit = _chapter_numbers(item.get("chapter_numbers"))
    if kind == "chapter_event":
        return explicit
    if kind == "milestone":
        return explicit or _scope_chapters(item.get("scope", "global"), [], total)
    return []


def assign_requirements_to_outline(requirements: dict | None, plan: dict) -> dict:
    """Attach each requirement to chapters and return the normalized snapshot.

    Existing explicit ``chapter_numbers`` are respected.  Scope is only a fallback,
    so an editor can move a requirement without losing its ownership on the next sync.
    """
    normalized = normalize_requirements(requirements)
    source_schema = _safe_int((requirements or {}).get("schema_version"))
    chapters = _chapter_items(plan)
    total = _total_chapters(plan, chapters)
    by_number: dict[int, dict] = {}
    previous_ids: dict[int, list[str]] = {}
    for index, chapter in enumerate(chapters, 1):
        number = _chapter_number(chapter, index)
        chapter.setdefault("chapter_num", number)
        previous_ids[number] = list(chapter.get("requirement_ids") or [])
        chapter["requirement_ids"] = []
        by_number[number] = chapter

    previous_assignments = {
        item["id"]: list(item.get("chapter_numbers") or [])
        for item in normalized["subtasks"]
    }
    for item in normalized["subtasks"]:
        owned = _owned_chapters(item, total)
        item["chapter_numbers"] = owned
        for number in owned:
            chapter = by_number.get(number)
            if chapter is not None and item["id"] not in chapter["requirement_ids"]:
                chapter["requirement_ids"].append(item["id"])

    current_ids = {
        number: list(chapter.get("requirement_ids") or [])
        for number, chapter in by_number.items()
    }
    assignments_changed = previous_assignments != {
        item["id"]: list(item.get("chapter_numbers") or [])
        for item in normalized["subtasks"]
    }
    outline_changed = previous_ids != current_ids
    migrated = source_schema < SCHEMA_VERSION
    if assignments_changed or outline_changed or migrated:
        normalized["version"] = int(normalized.get("version") or 1) + 1
        normalized["updated_at"] = _now()
    meta = plan.setdefault("_meta", {})
    meta_requirements = meta.setdefault("requirements", {})
    meta_requirements.update(
        {
            "schema_version": normalized["schema_version"],
            "version": normalized["version"],
            "subtask_count": normalized["total_count"],
            "p0_count": normalized["p0_count"],
            "updated_at": normalized["updated_at"],
        }
    )
    return normalized


def active_requirements(requirements: dict | None, chapter_num: int, total_chapters: int = 0) -> list[dict]:
    normalized = normalize_requirements(requirements)
    active = []
    for item in normalized["subtasks"]:
        if item.get("status") in {"resolved", "archived"}:
            continue
        kind = item["kind"]
        if kind == "planning_only":
            continue
        if kind in {"book_invariant", "preference"}:
            active.append(item)
            continue
        if chapter_num in _owned_chapters(item, total_chapters):
            active.append(item)
    return active


def evaluate_requirements_against_text(
    text: str, requirements: dict | None, *, chapter_num: int | None = None, total_chapters: int = 0
) -> dict:
    """Produce deterministic evidence for hard requirement markers.

    Criteria without checkable markers are reported as ``needs_review`` rather than
    silently passing.  This keeps the report honest while leaving semantic judgement
    to the existing LLM supervisor.
    """
    body = str(text or "")
    haystack = body.casefold()
    selected = (
        active_requirements(requirements, chapter_num, total_chapters)
        if chapter_num is not None
        else [
            item
            for item in normalize_requirements(requirements)["subtasks"]
            if item["kind"] != "planning_only"
        ]
    )
    results = []
    for item in selected:
        kind = item["kind"]
        includes = _as_list(item.get("must_include"))
        avoids = _as_list(item.get("must_avoid"))
        evidence = [term for term in includes if str(term).casefold() in haystack]
        absent = [term for term in includes if str(term).casefold() not in haystack]
        forbidden = [term for term in avoids if str(term).casefold() in haystack]
        not_required = []
        hard_gate = False

        if kind == "preference":
            missing = []
            not_required = absent
            passed = None
        elif kind == "book_invariant":
            missing = []
            not_required = absent
            passed = False if forbidden else True if avoids else None
            hard_gate = bool(forbidden and item.get("priority") == "P0")
        else:
            missing = absent
            checkable = bool(includes or avoids)
            passed = not missing and not forbidden if checkable else None
            hard_gate = bool(item.get("priority") == "P0")

        status = "passed" if passed is True else "failed" if passed is False else "needs_review"
        results.append(
            {
                "subtask_id": item["id"],
                "title": item["title"],
                "kind": kind,
                "priority": item["priority"],
                "hard_gate": hard_gate,
                "status": status,
                "passed": passed,
                "evidence": evidence,
                "missing": missing,
                "not_required_in_chapter": not_required,
                "forbidden": forbidden,
                "quality_criteria": item.get("quality_criteria", []),
            }
        )
    failed = [result for result in results if result["status"] == "failed"]
    review = [result for result in results if result["status"] == "needs_review"]
    overall = "failed" if failed else "needs_review" if review else "passed"
    return {
        "schema_version": SCHEMA_VERSION,
        "overall_status": overall,
        "overall_score": round(100 * (len(results) - len(failed)) / len(results)) if results else 100,
        "failed_count": len(failed),
        "hard_failure_count": sum(result["hard_gate"] for result in failed),
        "review_count": len(review),
        "results": results,
        "checked_at": _now(),
    }
