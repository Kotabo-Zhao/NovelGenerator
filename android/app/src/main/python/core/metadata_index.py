"""Transactional SQLite metadata mirror (pure stdlib sqlite3, no FTS5 for Chaquopy compatibility)."""
from __future__ import annotations

from contextlib import contextmanager
import json
import logging
import os
import sqlite3
import time
from typing import Any

log = logging.getLogger(__name__)


class MetadataIndex:
    """Lightweight transactional mirror of novel state metadata alongside Markdown/JSON files."""

    def __init__(self, novels_dir: str):
        self.novels_dir = novels_dir
        self.db_path = os.path.join(novels_dir, ".novelgen_meta.db")
        self._initialized = False

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        if self._initialized:
            return
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS novel_meta (
                novel_id TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                genre TEXT NOT NULL DEFAULT '',
                current_chapter INTEGER NOT NULL DEFAULT 0,
                completed_chapters_json TEXT NOT NULL DEFAULT '[]',
                state_json TEXT NOT NULL DEFAULT '{}',
                updated_at REAL NOT NULL DEFAULT 0
            )
            """
        )
        conn.commit()
        self._initialized = True

    @contextmanager
    def _connect(self):
        os.makedirs(self.novels_dir, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        try:
            self._ensure_schema(conn)
            yield conn
        finally:
            conn.close()

    def upsert_state(
        self,
        novel_id: str,
        state: dict[str, Any],
        *,
        title: str = "",
        genre: str = "",
    ) -> None:
        if not novel_id or not isinstance(state, dict):
            return
        completed = state.get("completed_chapters") or []
        current_ch = int(state.get("current_chapter") or 0)
        now = time.time()
        with self._connect() as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO novel_meta (
                        novel_id, title, genre, current_chapter,
                        completed_chapters_json, state_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(novel_id) DO UPDATE SET
                        title = CASE WHEN excluded.title != '' THEN excluded.title ELSE novel_meta.title END,
                        genre = CASE WHEN excluded.genre != '' THEN excluded.genre ELSE novel_meta.genre END,
                        current_chapter = excluded.current_chapter,
                        completed_chapters_json = excluded.completed_chapters_json,
                        state_json = excluded.state_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        novel_id,
                        str(title or ""),
                        str(genre or ""),
                        current_ch,
                        json.dumps(completed, ensure_ascii=False),
                        json.dumps(state, ensure_ascii=False),
                        now,
                    ),
                )

    def get_state(self, novel_id: str) -> dict[str, Any] | None:
        if not os.path.exists(self.db_path):
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT state_json FROM novel_meta WHERE novel_id = ?", (novel_id,)
            ).fetchone()
        if not row or not row[0]:
            return None
        try:
            data = json.loads(row[0])
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def list_novels(self) -> list[dict[str, Any]]:
        if not os.path.exists(self.db_path):
            return []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT novel_id, title, genre, current_chapter, completed_chapters_json, updated_at "
                "FROM novel_meta ORDER BY updated_at DESC"
            ).fetchall()
        out = []
        for nid, title, genre, cur_ch, comp_json, updated_at in rows:
            try:
                completed = json.loads(comp_json) if comp_json else []
            except Exception:
                completed = []
            out.append(
                {
                    "novel_id": nid,
                    "title": title,
                    "genre": genre,
                    "current_chapter": cur_ch,
                    "completed_chapters": completed,
                    "updated_at": updated_at,
                }
            )
        return out
