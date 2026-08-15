"""Shared contract for carrying creative requirements through planning and writing.

The contract is deliberately deterministic.  LLM supervision can add judgement, but
chapter ownership, prompt injection and the basic evidence report must remain stable
between workers and across retries.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable


SCHEMA_VERSION = 1


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
    """Return a copy with stable IDs and version metadata."""
    source = dict(requirements or {})
    subtasks = []
    for index, raw in enumerate(source.get("subtasks") or [], 1):
        item = dict(raw or {})
        item["id"] = str(item.get("id") or f"R{index:03d}")
        item["title"] = str(item.get("title") or "Unnamed requirement").strip()
        item["description"] = str(item.get("description") or "").strip()
        item["priority"] = str(item.get("priority") or "P1").upper()
        item["scope"] = str(item.get("scope") or "global").lower()
        item["must_include"] = _as_list(item.get("must_include"))
        item["must_avoid"] = _as_list(item.get("must_avoid"))
        item["quality_criteria"] = _as_list(item.get("quality_criteria"))
        item["generation_hint"] = str(item.get("generation_hint") or "").strip()
        item["status"] = str(item.get("status") or "pending")
        subtasks.append(item)

    source["schema_version"] = max(_safe_int(source.get("schema_version")), SCHEMA_VERSION)
    source["version"] = max(_safe_int(source.get("version")), 1)
    source["updated_at"] = source.get("updated_at") or _now()
    if novel_id:
        source["novel_id"] = novel_id
    source["subtasks"] = subtasks
    source["total_count"] = len(subtasks)
    source["p0_count"] = sum(item["priority"] == "P0" for item in subtasks)
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
    return list(range(1, total + 1))


def assign_requirements_to_outline(requirements: dict | None, plan: dict) -> dict:
    """Attach each requirement to chapters and return the normalized snapshot.

    Existing explicit ``chapter_numbers`` are respected.  Scope is only a fallback,
    so an editor can move a requirement without losing its ownership on the next sync.
    """
    normalized = normalize_requirements(requirements)
    chapters = _chapter_items(plan)
    total = _total_chapters(plan, chapters)
    by_number: dict[int, dict] = {}
    for index, chapter in enumerate(chapters, 1):
        number = _chapter_number(chapter, index)
        chapter.setdefault("chapter_num", number)
        chapter["requirement_ids"] = []
        by_number[number] = chapter

    for item in normalized["subtasks"]:
        explicit = []
        for value in _as_list(item.get("chapter_numbers")):
            try:
                number = int(value)
                if number > 0:
                    explicit.append(number)
            except (TypeError, ValueError):
                continue
        owned = _scope_chapters(item["scope"], explicit, total)
        item["chapter_numbers"] = owned
        for number in owned:
            chapter = by_number.get(number)
            if chapter is not None and item["id"] not in chapter["requirement_ids"]:
                chapter["requirement_ids"].append(item["id"])

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
        owned = item.get("chapter_numbers") or _scope_chapters(item["scope"], [], total_chapters)
        if not owned or chapter_num in owned:
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
        else normalize_requirements(requirements)["subtasks"]
    )
    results = []
    for item in selected:
        includes = _as_list(item.get("must_include"))
        avoids = _as_list(item.get("must_avoid"))
        evidence = [term for term in includes if str(term).casefold() in haystack]
        missing = [term for term in includes if str(term).casefold() not in haystack]
        forbidden = [term for term in avoids if str(term).casefold() in haystack]
        checkable = bool(includes or avoids)
        passed = not missing and not forbidden if checkable else None
        status = "passed" if passed is True else "failed" if passed is False else "needs_review"
        results.append(
            {
                "subtask_id": item["id"],
                "title": item["title"],
                "priority": item["priority"],
                "status": status,
                "passed": passed,
                "evidence": evidence,
                "missing": missing,
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
        "review_count": len(review),
        "results": results,
        "checked_at": _now(),
    }
