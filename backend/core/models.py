"""Typed domain models for NovelGenerator v5."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


@dataclass
class ChapterContract:
    chapter_num: int
    title: str
    summary: str
    goal: str = ""
    conflict: str = ""
    characters: list[str] = field(default_factory=list)
    must_happen: list[str] = field(default_factory=list)
    hook_direction: str = ""
    target_words: int = 2000
    pov: str = "第三人称限知"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChapterContract":
        return cls(
            chapter_num=int(data.get("chapter_num", 1)),
            title=str(data.get("title", "")).strip() or f"第{data.get('chapter_num', 1)}章",
            summary=str(data.get("summary", "")).strip(),
            goal=str(data.get("goal", "")).strip(),
            conflict=str(data.get("conflict", "")).strip(),
            characters=[str(c).strip() for c in (data.get("characters") or []) if str(c).strip()],
            must_happen=[str(e).strip() for e in (data.get("must_happen") or []) if str(e).strip()],
            hook_direction=str(data.get("hook_direction", "")).strip(),
            target_words=max(200, int(data.get("target_words", 2000) or 2000)),
            pov=str(data.get("pov", "第三人称限知")).strip() or "第三人称限知",
        )


@dataclass
class StateDelta:
    """Structured state transition committed atomically after a chapter is finalized."""

    chapter_num: int
    bridge_summary: str
    ending_anchor: str = ""
    entity_updates: list[dict[str, Any]] = field(default_factory=list)
    foreshadow_updates: list[dict[str, Any]] = field(default_factory=list)
    canon_events: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LLMSettings:
    mode: Literal["demo", "openai_compatible"] = "demo"
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"
    api_key: str = ""
    temperature: float = 0.8
    max_tokens: int = 4096

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "base_url": self.base_url,
            "model": self.model,
            "has_api_key": bool(self.api_key),
            "api_key_masked": (self.api_key[:4] + "****" + self.api_key[-4:]) if len(self.api_key) >= 10 else ("****" if self.api_key else ""),
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
