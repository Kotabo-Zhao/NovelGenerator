"""Fast UTF-8 TXT, Markdown, and full JSON ledger exporters."""

from __future__ import annotations

import json
from typing import Any

from .database import NarrativeLedgerDB


def export_novel_text(db: NarrativeLedgerDB, novel_id: str, fmt: str = "txt") -> tuple[str, str, str]:
    novel = db.get_novel(novel_id)
    if not novel:
        raise ValueError("作品不存在")

    title = novel["title"]
    chapters_full = [db.get_chapter(novel_id, ch["chapter_num"]) for ch in novel["chapters"]]
    completed = [c for c in chapters_full if c and c.get("content")]

    if fmt == "json":
        payload = {
            "novel": novel,
            "chapters": completed,
            "ledger": db.get_ledger(novel_id),
        }
        return (
            json.dumps(payload, ensure_ascii=False, indent=2),
            "application/json; charset=utf-8",
            f"{title}_ledger_backup.json",
        )

    if fmt == "md":
        lines = [f"# {title}\n", f"> 题材：{novel['genre']} · 风格：{novel['style']}\n"]
        if novel.get("premise"):
            lines.append(f"{novel['premise']}\n")
        for ch in completed:
            lines.append(f"\n## 第{ch['chapter_num']}章 {ch['title']}\n\n{ch['content']}\n")
        return "\n".join(lines), "text/markdown; charset=utf-8", f"{title}.md"

    # Default UTF-8 TXT
    lines = [title, "=" * 32, f"题材：{novel['genre']}  风格：{novel['style']}", ""]
    for ch in completed:
        lines.append(f"第{ch['chapter_num']}章 {ch['title']}")
        lines.append("")
        lines.append(ch["content"])
        lines.append("\n")
    return "\n".join(lines), "text/plain; charset=utf-8", f"{title}.txt"
