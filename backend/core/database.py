"""Unified SQLite (WAL + FTS5) Narrative State Ledger for NovelGenerator v5.

Replaces fragmented flat JSON files (SharedMemoryManager + char_memory +
worldinfo) with a single local, zero-dependency, ACID-transactional database.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .models import ChapterContract, StateDelta


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;

CREATE TABLE IF NOT EXISTS novels (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    genre TEXT NOT NULL DEFAULT '都市悬疑',
    style TEXT NOT NULL DEFAULT '克制镜头',
    mode TEXT NOT NULL DEFAULT 'web',
    premise TEXT NOT NULL DEFAULT '',
    pov TEXT NOT NULL DEFAULT '第三人称限知',
    target_chapters INTEGER NOT NULL DEFAULT 20,
    target_words_per_chapter INTEGER NOT NULL DEFAULT 2000,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chapters (
    novel_id TEXT NOT NULL,
    chapter_num INTEGER NOT NULL,
    title TEXT NOT NULL,
    contract_json TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    word_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'planned',
    bridge_summary TEXT NOT NULL DEFAULT '',
    ending_anchor TEXT NOT NULL DEFAULT '',
    craft_score INTEGER,
    ai_score INTEGER,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (novel_id, chapter_num),
    FOREIGN KEY (novel_id) REFERENCES novels(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS chapter_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    novel_id TEXT NOT NULL,
    chapter_num INTEGER NOT NULL,
    content TEXT NOT NULL,
    word_count INTEGER NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY (novel_id, chapter_num) REFERENCES chapters(novel_id, chapter_num) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS narrative_entities (
    id TEXT PRIMARY KEY,
    novel_id TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'character',
    name TEXT NOT NULL,
    aliases TEXT NOT NULL DEFAULT '',
    profile TEXT NOT NULL DEFAULT '',
    current_state TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    voice_notes TEXT NOT NULL DEFAULT '',
    last_seen_chapter INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    UNIQUE (novel_id, kind, name),
    FOREIGN KEY (novel_id) REFERENCES novels(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS foreshadows (
    id TEXT PRIMARY KEY,
    novel_id TEXT NOT NULL,
    title TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'planted',
    planted_chapter INTEGER NOT NULL DEFAULT 1,
    target_chapter INTEGER NOT NULL DEFAULT 5,
    resolved_chapter INTEGER,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (novel_id) REFERENCES novels(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS canon_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    novel_id TEXT NOT NULL,
    chapter_num INTEGER NOT NULL,
    summary TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (novel_id) REFERENCES novels(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS world_entries (
    id TEXT PRIMARY KEY,
    novel_id TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'rule',
    title TEXT NOT NULL,
    keywords TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    FOREIGN KEY (novel_id) REFERENCES novels(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ledger_search_index (
    novel_id TEXT NOT NULL,
    doc_type TEXT NOT NULL,
    ref_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (novel_id, doc_type, ref_id)
);
CREATE INDEX IF NOT EXISTS idx_ledger_search_novel ON ledger_search_index (novel_id);
"""

FTS5_SCHEMA_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS ledger_fts USING fts5(
    novel_id UNINDEXED,
    doc_type UNINDEXED,
    ref_id UNINDEXED,
    title,
    body,
    tokenize = 'unicode61'
);
"""


class NarrativeLedgerDB:
    """Thread-safe SQLite WAL + FTS5 repository for novels and narrative state."""

    def __init__(self, db_path: str | Path, *, enable_fts5: bool = True):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.has_fts5 = False
        self._init_schema(enable_fts5=enable_fts5)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE;")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self, *, enable_fts5: bool = True) -> None:
        conn = self._connect()
        try:
            conn.executescript(SCHEMA_SQL)
            if enable_fts5:
                try:
                    conn.executescript(FTS5_SCHEMA_SQL)
                    self.has_fts5 = True
                except sqlite3.OperationalError:
                    # Android Chaquopy libsqlite3 is compiled without FTS5; fall back cleanly.
                    self.has_fts5 = False
            conn.commit()
        finally:
            conn.close()

    def _upsert_fts(self, conn: sqlite3.Connection, novel_id: str, doc_type: str, ref_id: str, title: str, body: str) -> None:
        conn.execute(
            """
            INSERT INTO ledger_search_index (novel_id, doc_type, ref_id, title, body)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(novel_id, doc_type, ref_id) DO UPDATE SET
                title = excluded.title,
                body = excluded.body
            """,
            (novel_id, doc_type, ref_id, title, body),
        )
        if self.has_fts5:
            conn.execute(
                "DELETE FROM ledger_fts WHERE novel_id = ? AND doc_type = ? AND ref_id = ?",
                (novel_id, doc_type, ref_id),
            )
            conn.execute(
                "INSERT INTO ledger_fts (novel_id, doc_type, ref_id, title, body) VALUES (?, ?, ?, ?, ?)",
                (novel_id, doc_type, ref_id, title, body),
            )

    # ── Novels ──

    def create_novel(
        self,
        *,
        title: str,
        genre: str = "都市悬疑",
        style: str = "克制镜头",
        mode: str = "web",
        premise: str = "",
        pov: str = "第三人称限知",
        target_chapters: int = 20,
        target_words_per_chapter: int = 2000,
    ) -> dict[str, Any]:
        novel_id = uuid.uuid4().hex[:12]
        now = _utc_now()
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO novels (id, title, genre, style, mode, premise, pov, target_chapters, target_words_per_chapter, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (novel_id, title.strip(), genre, style, mode, premise.strip(), pov, target_chapters, target_words_per_chapter, now, now),
            )
        return self.get_novel(novel_id)  # type: ignore[return-value]

    def list_novels(self) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT n.*,
                       COALESCE(SUM(c.word_count), 0) AS total_words,
                       COALESCE(SUM(CASE WHEN c.status = 'completed' THEN 1 ELSE 0 END), 0) AS completed_chapters,
                       COUNT(c.chapter_num) AS planned_chapters
                FROM novels n
                LEFT JOIN chapters c ON c.novel_id = n.id
                GROUP BY n.id
                ORDER BY n.updated_at DESC
                """
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_novel(self, novel_id: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM novels WHERE id = ?", (novel_id,)).fetchone()
            if not row:
                return None
            novel = dict(row)
            chapters = conn.execute(
                """
                SELECT chapter_num, title, contract_json, word_count, status,
                       bridge_summary, ending_anchor, craft_score, ai_score, updated_at
                FROM chapters WHERE novel_id = ? ORDER BY chapter_num ASC
                """,
                (novel_id,),
            ).fetchall()
            novel["chapters"] = [
                {
                    **dict(c),
                    "contract": json.loads(c["contract_json"]),
                }
                for c in chapters
            ]
            novel["total_words"] = sum(c["word_count"] for c in chapters)
            novel["completed_chapters"] = sum(1 for c in chapters if c["status"] == "completed")
            return novel
        finally:
            conn.close()

    def delete_novel(self, novel_id: str) -> bool:
        with self.transaction() as conn:
            conn.execute("DELETE FROM ledger_search_index WHERE novel_id = ?", (novel_id,))
            if self.has_fts5:
                conn.execute("DELETE FROM ledger_fts WHERE novel_id = ?", (novel_id,))
            cur = conn.execute("DELETE FROM novels WHERE id = ?", (novel_id,))
            return cur.rowcount > 0

    # ── Chapters & Contracts ──

    def save_chapter_contracts(self, novel_id: str, contracts: list[ChapterContract]) -> None:
        now = _utc_now()
        with self.transaction() as conn:
            for contract in contracts:
                existing = conn.execute(
                    "SELECT status FROM chapters WHERE novel_id = ? AND chapter_num = ?",
                    (novel_id, contract.chapter_num),
                ).fetchone()
                cjson = json.dumps(contract.to_dict(), ensure_ascii=False)
                if existing:
                    conn.execute(
                        "UPDATE chapters SET title = ?, contract_json = ?, updated_at = ? WHERE novel_id = ? AND chapter_num = ?",
                        (contract.title, cjson, now, novel_id, contract.chapter_num),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO chapters (novel_id, chapter_num, title, contract_json, updated_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (novel_id, contract.chapter_num, contract.title, cjson, now),
                    )
            conn.execute("UPDATE novels SET updated_at = ? WHERE id = ?", (now, novel_id))

    def get_chapter(self, novel_id: str, chapter_num: int) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM chapters WHERE novel_id = ? AND chapter_num = ?",
                (novel_id, chapter_num),
            ).fetchone()
            if not row:
                return None
            item = dict(row)
            item["contract"] = json.loads(item["contract_json"])
            return item
        finally:
            conn.close()

    def commit_chapter_and_state(
        self,
        novel_id: str,
        chapter_num: int,
        *,
        content: str,
        craft_score: int | None = None,
        ai_score: int | None = None,
        version_note: str = "章节定稿",
        delta: StateDelta | None = None,
    ) -> dict[str, Any]:
        """Atomically commit chapter prose, version snapshot, FTS index, and narrative state deltas."""
        now = _utc_now()
        clean = content.strip()
        word_count = len("".join(clean.split()))
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT title, contract_json FROM chapters WHERE novel_id = ? AND chapter_num = ?",
                (novel_id, chapter_num),
            ).fetchone()
            if not row:
                default_contract = ChapterContract(chapter_num=chapter_num, title=f"第{chapter_num}章", summary="")
                conn.execute(
                    "INSERT INTO chapters (novel_id, chapter_num, title, contract_json, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (novel_id, chapter_num, default_contract.title, json.dumps(default_contract.to_dict(), ensure_ascii=False), now),
                )
                title = default_contract.title
            else:
                title = row["title"]

            bridge_summary = delta.bridge_summary if delta else ""
            ending_anchor = delta.ending_anchor if delta else (clean[-120:] if clean else "")

            conn.execute(
                """
                UPDATE chapters
                SET content = ?, word_count = ?, status = 'completed',
                    bridge_summary = CASE WHEN ? != '' THEN ? ELSE bridge_summary END,
                    ending_anchor = CASE WHEN ? != '' THEN ? ELSE ending_anchor END,
                    craft_score = COALESCE(?, craft_score),
                    ai_score = COALESCE(?, ai_score),
                    updated_at = ?
                WHERE novel_id = ? AND chapter_num = ?
                """,
                (
                    clean, word_count,
                    bridge_summary, bridge_summary,
                    ending_anchor, ending_anchor,
                    craft_score, ai_score,
                    now, novel_id, chapter_num,
                ),
            )
            conn.execute(
                """
                INSERT INTO chapter_versions (novel_id, chapter_num, content, word_count, note, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (novel_id, chapter_num, clean, word_count, version_note, now),
            )
            self._upsert_fts(conn, novel_id, "chapter", str(chapter_num), f"第{chapter_num}章 {title}", clean)

            if delta:
                for ent in delta.entity_updates:
                    name = str(ent.get("name", "")).strip()
                    if not name:
                        continue
                    kind = str(ent.get("kind", "character")).strip() or "character"
                    existing_ent = conn.execute(
                        "SELECT id, profile, current_state, location, voice_notes FROM narrative_entities WHERE novel_id = ? AND kind = ? AND name = ?",
                        (novel_id, kind, name),
                    ).fetchone()
                    if existing_ent:
                        new_state = str(ent.get("current_state") or existing_ent["current_state"]).strip()
                        new_loc = str(ent.get("location") or existing_ent["location"]).strip()
                        new_profile = str(ent.get("profile") or existing_ent["profile"]).strip()
                        new_voice = str(ent.get("voice_notes") or existing_ent["voice_notes"]).strip()
                        conn.execute(
                            """
                            UPDATE narrative_entities
                            SET profile = ?, current_state = ?, location = ?, voice_notes = ?, last_seen_chapter = ?, updated_at = ?
                            WHERE id = ?
                            """,
                            (new_profile, new_state, new_loc, new_voice, chapter_num, now, existing_ent["id"]),
                        )
                        self._upsert_fts(conn, novel_id, "entity", existing_ent["id"], name, f"{new_profile} {new_state} {new_loc}")
                    else:
                        ent_id = uuid.uuid4().hex[:10]
                        profile = str(ent.get("profile", "")).strip()
                        state = str(ent.get("current_state", "")).strip()
                        location = str(ent.get("location", "")).strip()
                        voice = str(ent.get("voice_notes", "")).strip()
                        conn.execute(
                            """
                            INSERT INTO narrative_entities (id, novel_id, kind, name, aliases, profile, current_state, location, voice_notes, last_seen_chapter, updated_at)
                            VALUES (?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?)
                            """,
                            (ent_id, novel_id, kind, name, profile, state, location, voice, chapter_num, now),
                        )
                        self._upsert_fts(conn, novel_id, "entity", ent_id, name, f"{profile} {state} {location}")

                for fs in delta.foreshadow_updates:
                    fs_title = str(fs.get("title", "")).strip()
                    if not fs_title:
                        continue
                    fs_status = str(fs.get("status", "planted")).strip()
                    existing_fs = conn.execute(
                        "SELECT id FROM foreshadows WHERE novel_id = ? AND title = ?",
                        (novel_id, fs_title),
                    ).fetchone()
                    if existing_fs:
                        conn.execute(
                            """
                            UPDATE foreshadows
                            SET status = ?, detail = COALESCE(NULLIF(?, ''), detail),
                                resolved_chapter = CASE WHEN ? = 'resolved' THEN ? ELSE resolved_chapter END,
                                updated_at = ?
                            WHERE id = ?
                            """,
                            (fs_status, str(fs.get("detail", "")).strip(), fs_status, chapter_num, now, existing_fs["id"]),
                        )
                    else:
                        fs_id = uuid.uuid4().hex[:10]
                        conn.execute(
                            """
                            INSERT INTO foreshadows (id, novel_id, title, detail, status, planted_chapter, target_chapter, resolved_chapter, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                fs_id, novel_id, fs_title, str(fs.get("detail", "")).strip(),
                                fs_status, chapter_num, int(fs.get("target_chapter", chapter_num + 3) or chapter_num + 3),
                                chapter_num if fs_status == "resolved" else None, now,
                            ),
                        )

                if delta.canon_events:
                    conn.execute("DELETE FROM canon_events WHERE novel_id = ? AND chapter_num = ?", (novel_id, chapter_num))
                    for ev in delta.canon_events:
                        ev_clean = str(ev).strip()
                        if ev_clean:
                            conn.execute(
                                "INSERT INTO canon_events (novel_id, chapter_num, summary, created_at) VALUES (?, ?, ?, ?)",
                                (novel_id, chapter_num, ev_clean, now),
                            )

            conn.execute("UPDATE novels SET updated_at = ? WHERE id = ?", (now, novel_id))

        return self.get_chapter(novel_id, chapter_num)  # type: ignore[return-value]

    def list_chapter_versions(self, novel_id: str, chapter_num: int) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, word_count, note, created_at, substr(content, 1, 140) AS preview FROM chapter_versions WHERE novel_id = ? AND chapter_num = ? ORDER BY id DESC LIMIT 20",
                (novel_id, chapter_num),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def restore_chapter_version(self, novel_id: str, chapter_num: int, version_id: int) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT content, note FROM chapter_versions WHERE id = ? AND novel_id = ? AND chapter_num = ?",
                (version_id, novel_id, chapter_num),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return None
        return self.commit_chapter_and_state(
            novel_id,
            chapter_num,
            content=row["content"],
            version_note=f"回滚至版本 #{version_id}",
        )

    # ── Narrative Ledger CRUD & FTS5 Search ──

    def upsert_entity(self, novel_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        now = _utc_now()
        ent_id = str(payload.get("id") or "").strip() or uuid.uuid4().hex[:10]
        kind = str(payload.get("kind", "character")).strip() or "character"
        name = str(payload.get("name", "")).strip()
        if not name:
            raise ValueError("实体名称不能为空")
        aliases = str(payload.get("aliases", "")).strip()
        profile = str(payload.get("profile", "")).strip()
        current_state = str(payload.get("current_state", "")).strip()
        location = str(payload.get("location", "")).strip()
        voice_notes = str(payload.get("voice_notes", "")).strip()
        last_seen = int(payload.get("last_seen_chapter", 0) or 0)

        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO narrative_entities (id, novel_id, kind, name, aliases, profile, current_state, location, voice_notes, last_seen_chapter, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(novel_id, kind, name) DO UPDATE SET
                    aliases = excluded.aliases,
                    profile = excluded.profile,
                    current_state = excluded.current_state,
                    location = excluded.location,
                    voice_notes = excluded.voice_notes,
                    last_seen_chapter = MAX(narrative_entities.last_seen_chapter, excluded.last_seen_chapter),
                    updated_at = excluded.updated_at
                """,
                (ent_id, novel_id, kind, name, aliases, profile, current_state, location, voice_notes, last_seen, now),
            )
            row = conn.execute(
                "SELECT * FROM narrative_entities WHERE novel_id = ? AND kind = ? AND name = ?",
                (novel_id, kind, name),
            ).fetchone()
            self._upsert_fts(conn, novel_id, "entity", row["id"], f"{name} {aliases}", f"{profile} {current_state} {location} {voice_notes}")
            return dict(row)

    def upsert_foreshadow(self, novel_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        now = _utc_now()
        fs_id = str(payload.get("id") or "").strip() or uuid.uuid4().hex[:10]
        title = str(payload.get("title", "")).strip()
        if not title:
            raise ValueError("伏笔标题不能为空")
        detail = str(payload.get("detail", "")).strip()
        status = str(payload.get("status", "planted")).strip() or "planted"
        planted = int(payload.get("planted_chapter", 1) or 1)
        target = int(payload.get("target_chapter", planted + 3) or planted + 3)
        resolved = payload.get("resolved_chapter")
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO foreshadows (id, novel_id, title, detail, status, planted_chapter, target_chapter, resolved_chapter, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    detail = excluded.detail,
                    status = excluded.status,
                    target_chapter = excluded.target_chapter,
                    resolved_chapter = excluded.resolved_chapter,
                    updated_at = excluded.updated_at
                """,
                (fs_id, novel_id, title, detail, status, planted, target, resolved, now),
            )
            self._upsert_fts(conn, novel_id, "foreshadow", fs_id, title, detail)
            row = conn.execute("SELECT * FROM foreshadows WHERE id = ?", (fs_id,)).fetchone()
            return dict(row)

    def upsert_world_entry(self, novel_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        now = _utc_now()
        wid = str(payload.get("id") or "").strip() or uuid.uuid4().hex[:10]
        category = str(payload.get("category", "rule")).strip() or "rule"
        title = str(payload.get("title", "")).strip()
        if not title:
            raise ValueError("世界书条目标题不能为空")
        keywords = str(payload.get("keywords", "")).strip()
        content = str(payload.get("content", "")).strip()
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO world_entries (id, novel_id, category, title, keywords, content, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    category = excluded.category,
                    title = excluded.title,
                    keywords = excluded.keywords,
                    content = excluded.content,
                    updated_at = excluded.updated_at
                """,
                (wid, novel_id, category, title, keywords, content, now),
            )
            self._upsert_fts(conn, novel_id, "world", wid, f"{title} {keywords}", content)
            row = conn.execute("SELECT * FROM world_entries WHERE id = ?", (wid,)).fetchone()
            return dict(row)

    def get_ledger(self, novel_id: str) -> dict[str, Any]:
        conn = self._connect()
        try:
            entities = [dict(r) for r in conn.execute("SELECT * FROM narrative_entities WHERE novel_id = ? ORDER BY kind, last_seen_chapter DESC, name", (novel_id,)).fetchall()]
            foreshadows = [dict(r) for r in conn.execute("SELECT * FROM foreshadows WHERE novel_id = ? ORDER BY status != 'resolved' DESC, target_chapter ASC", (novel_id,)).fetchall()]
            world_entries = [dict(r) for r in conn.execute("SELECT * FROM world_entries WHERE novel_id = ? ORDER BY category, title", (novel_id,)).fetchall()]
            canon_events = [dict(r) for r in conn.execute("SELECT * FROM canon_events WHERE novel_id = ? ORDER BY chapter_num ASC, id ASC", (novel_id,)).fetchall()]
            return {
                "entities": entities,
                "foreshadows": foreshadows,
                "world_entries": world_entries,
                "canon_events": canon_events,
            }
        finally:
            conn.close()

    def search_ledger(self, novel_id: str, query: str, limit: int = 8) -> list[dict[str, Any]]:
        """Fast FTS5 search with safe LIKE fallback for short CJK queries."""
        q = (query or "").strip()
        if not q:
            return []
        conn = self._connect()
        try:
            safe_tokens = [tok for tok in q.replace('"', " ").split() if tok]
            results: list[dict[str, Any]] = []
            if self.has_fts5 and safe_tokens:
                fts_query = " OR ".join(f'"{t}"' for t in safe_tokens[:6])
                try:
                    rows = conn.execute(
                        """
                        SELECT doc_type, ref_id, title, substr(body, 1, 220) AS snippet
                        FROM ledger_fts
                        WHERE novel_id = ? AND ledger_fts MATCH ?
                        LIMIT ?
                        """,
                        (novel_id, fts_query, limit),
                    ).fetchall()
                    results = [dict(r) for r in rows]
                except sqlite3.OperationalError:
                    results = []
            if not results:
                like_pat = f"%{q}%"
                rows = conn.execute(
                    """
                    SELECT doc_type, ref_id, title, substr(body, 1, 220) AS snippet
                    FROM ledger_search_index
                    WHERE novel_id = ? AND (title LIKE ? OR body LIKE ?)
                    LIMIT ?
                    """,
                    (novel_id, like_pat, like_pat, limit),
                ).fetchall()
                results = [dict(r) for r in rows]
            return results
        finally:
            conn.close()
