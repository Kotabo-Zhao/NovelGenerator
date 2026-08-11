"""Durable owner-to-novel bindings for the authenticated API surface."""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional

from .path_security import validate_novel_id


class OwnershipError(PermissionError):
    """Raised when a novel is missing or belongs to another owner."""


class OwnershipStore:
    """SQLite ownership index kept separate from legacy plan files."""

    def __init__(self, novels_dir: os.PathLike[str] | str, db_name: str = "novel_ownership.db"):
        self._root = Path(novels_dir)
        self._root.mkdir(parents=True, exist_ok=True)
        self._db_path = self._root / db_name
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self._db_path), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    @contextmanager
    def _session(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _owner(owner_id: str) -> str:
        if not isinstance(owner_id, str):
            raise ValueError("owner_id must be a string")
        normalized = owner_id.strip()
        if not normalized or len(normalized) > 200:
            raise ValueError("owner_id must not be empty or too long")
        return normalized

    @staticmethod
    def _key(idempotency_key: str) -> str:
        if not isinstance(idempotency_key, str):
            raise ValueError("idempotency_key must be a string")
        normalized = idempotency_key.strip()
        if not normalized or len(normalized) > 200:
            raise ValueError("idempotency_key must not be empty or too long")
        return normalized

    @staticmethod
    def _request_hash(request_hash: str) -> str:
        if not isinstance(request_hash, str) or len(request_hash) != 64:
            raise ValueError("request_hash must be a SHA-256 hex digest")
        try:
            int(request_hash, 16)
        except ValueError as exc:
            raise ValueError("request_hash must be a SHA-256 hex digest") from exc
        return request_hash.lower()

    @staticmethod
    def _novel(novel_id: str) -> str:
        try:
            return validate_novel_id(novel_id)
        except ValueError:
            raise

    def _initialize(self) -> None:
        with self._lock, self._session() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS novel_ownership (
                    novel_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    deleted_at REAL
                );
                CREATE INDEX IF NOT EXISTS idx_novel_ownership_owner
                    ON novel_ownership(owner_id, deleted_at, created_at);
                CREATE TABLE IF NOT EXISTS novel_creation_requests (
                    owner_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    novel_id TEXT,
                    error_message TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(owner_id, idempotency_key)
                );
                """
            )

    @staticmethod
    def _record(row: sqlite3.Row) -> Dict[str, object]:
        return {
            "novel_id": row["novel_id"],
            "owner_id": row["owner_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "deleted_at": row["deleted_at"],
        }

    def claim(self, novel_id: str, owner_id: str) -> Dict[str, object]:
        """Bind a newly created novel; never silently reassign an existing one."""

        normalized_novel = self._novel(novel_id)
        normalized_owner = self._owner(owner_id)
        with self._lock, self._session() as connection:
            row = connection.execute(
                "SELECT * FROM novel_ownership WHERE novel_id = ?",
                (normalized_novel,),
            ).fetchone()
            if row is not None:
                if row["owner_id"] != normalized_owner or row["deleted_at"] is not None:
                    raise OwnershipError("novel is not available to this owner")
                return self._record(row)

            now = time.time()
            try:
                connection.execute(
                    """
                    INSERT INTO novel_ownership
                        (novel_id, owner_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (normalized_novel, normalized_owner, now, now),
                )
            except sqlite3.IntegrityError as exc:
                raise OwnershipError("novel ownership could not be claimed") from exc
            row = connection.execute(
                "SELECT * FROM novel_ownership WHERE novel_id = ?",
                (normalized_novel,),
            ).fetchone()
            if row is None:
                raise RuntimeError("novel ownership could not be loaded after claim")
            return self._record(row)

    def require_owner(self, novel_id: str, owner_id: str) -> Dict[str, object]:
        normalized_novel = self._novel(novel_id)
        normalized_owner = self._owner(owner_id)
        with self._lock, self._session() as connection:
            row = connection.execute(
                """
                SELECT * FROM novel_ownership
                WHERE novel_id = ? AND owner_id = ? AND deleted_at IS NULL
                """,
                (normalized_novel, normalized_owner),
            ).fetchone()
            if row is None:
                raise OwnershipError("novel is not owned by this user")
            return self._record(row)

    def owns(self, novel_id: str, owner_id: str) -> bool:
        try:
            self.require_owner(novel_id, owner_id)
            return True
        except OwnershipError:
            return False

    def list_for_owner(self, owner_id: str) -> List[Dict[str, object]]:
        normalized_owner = self._owner(owner_id)
        with self._lock, self._session() as connection:
            rows = connection.execute(
                """
                SELECT * FROM novel_ownership
                WHERE owner_id = ? AND deleted_at IS NULL
                ORDER BY created_at ASC, novel_id ASC
                """,
                (normalized_owner,),
            ).fetchall()
            return [self._record(row) for row in rows]

    def mark_deleted(self, novel_id: str, owner_id: str) -> Dict[str, object]:
        current = self.require_owner(novel_id, owner_id)
        now = time.time()
        with self._lock, self._session() as connection:
            connection.execute(
                """
                UPDATE novel_ownership
                SET deleted_at = ?, updated_at = ?
                WHERE novel_id = ? AND owner_id = ? AND deleted_at IS NULL
                """,
                (now, now, current["novel_id"], current["owner_id"]),
            )
            row = connection.execute(
                "SELECT * FROM novel_ownership WHERE novel_id = ?",
                (current["novel_id"],),
            ).fetchone()
            if row is None:
                raise RuntimeError("novel ownership disappeared during delete")
            return self._record(row)

    @staticmethod
    def _creation_record(row: sqlite3.Row) -> Dict[str, object]:
        return {
            "owner_id": row["owner_id"],
            "idempotency_key": row["idempotency_key"],
            "request_hash": row["request_hash"],
            "status": row["status"],
            "novel_id": row["novel_id"],
            "error_message": row["error_message"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def reserve_creation(
        self,
        owner_id: str,
        idempotency_key: str,
        request_hash: str,
    ) -> Dict[str, object]:
        """Reserve a create request so network retries cannot duplicate planning."""

        normalized_owner = self._owner(owner_id)
        normalized_key = self._key(idempotency_key)
        normalized_hash = self._request_hash(request_hash)
        with self._lock, self._session() as connection:
            row = connection.execute(
                """
                SELECT * FROM novel_creation_requests
                WHERE owner_id = ? AND idempotency_key = ?
                """,
                (normalized_owner, normalized_key),
            ).fetchone()
            now = time.time()
            if row is not None:
                if row["request_hash"] != normalized_hash:
                    raise ValueError("idempotency_key is already bound to another request")
                if row["status"] == "failed":
                    connection.execute(
                        """
                        UPDATE novel_creation_requests
                        SET status = 'running', error_message = NULL, updated_at = ?
                        WHERE owner_id = ? AND idempotency_key = ?
                        """,
                        (now, normalized_owner, normalized_key),
                    )
                    row = connection.execute(
                        """
                        SELECT * FROM novel_creation_requests
                        WHERE owner_id = ? AND idempotency_key = ?
                        """,
                        (normalized_owner, normalized_key),
                    ).fetchone()
                record = self._creation_record(row)
                record["replayed"] = True
                return record

            connection.execute(
                """
                INSERT INTO novel_creation_requests
                    (owner_id, idempotency_key, request_hash, status, created_at, updated_at)
                VALUES (?, ?, ?, 'running', ?, ?)
                """,
                (normalized_owner, normalized_key, normalized_hash, now, now),
            )
            row = connection.execute(
                """
                SELECT * FROM novel_creation_requests
                WHERE owner_id = ? AND idempotency_key = ?
                """,
                (normalized_owner, normalized_key),
            ).fetchone()
            if row is None:
                raise RuntimeError("creation reservation could not be loaded")
            record = self._creation_record(row)
            record["replayed"] = False
            return record

    def complete_creation(
        self,
        owner_id: str,
        idempotency_key: str,
        request_hash: str,
        novel_id: str,
    ) -> Dict[str, object]:
        normalized_owner = self._owner(owner_id)
        normalized_key = self._key(idempotency_key)
        normalized_hash = self._request_hash(request_hash)
        normalized_novel = self._novel(novel_id)
        with self._lock, self._session() as connection:
            row = connection.execute(
                """
                SELECT * FROM novel_creation_requests
                WHERE owner_id = ? AND idempotency_key = ?
                """,
                (normalized_owner, normalized_key),
            ).fetchone()
            if row is None or row["request_hash"] != normalized_hash:
                raise ValueError("creation reservation does not match request")
            now = time.time()
            connection.execute(
                """
                UPDATE novel_creation_requests
                SET status = 'succeeded', novel_id = ?, error_message = NULL, updated_at = ?
                WHERE owner_id = ? AND idempotency_key = ?
                """,
                (normalized_novel, now, normalized_owner, normalized_key),
            )
            updated = connection.execute(
                """
                SELECT * FROM novel_creation_requests
                WHERE owner_id = ? AND idempotency_key = ?
                """,
                (normalized_owner, normalized_key),
            ).fetchone()
            if updated is None:
                raise RuntimeError("completed creation reservation could not be loaded")
            return self._creation_record(updated)

    def fail_creation(
        self,
        owner_id: str,
        idempotency_key: str,
        request_hash: str,
        error_message: str,
    ) -> Dict[str, object]:
        normalized_owner = self._owner(owner_id)
        normalized_key = self._key(idempotency_key)
        normalized_hash = self._request_hash(request_hash)
        if not isinstance(error_message, str) or not error_message.strip():
            raise ValueError("error_message must not be empty")
        with self._lock, self._session() as connection:
            row = connection.execute(
                """
                SELECT * FROM novel_creation_requests
                WHERE owner_id = ? AND idempotency_key = ?
                """,
                (normalized_owner, normalized_key),
            ).fetchone()
            if row is None or row["request_hash"] != normalized_hash:
                raise ValueError("creation reservation does not match request")
            now = time.time()
            connection.execute(
                """
                UPDATE novel_creation_requests
                SET status = 'failed', error_message = ?, updated_at = ?
                WHERE owner_id = ? AND idempotency_key = ?
                """,
                (error_message.strip()[:2000], now, normalized_owner, normalized_key),
            )
            updated = connection.execute(
                """
                SELECT * FROM novel_creation_requests
                WHERE owner_id = ? AND idempotency_key = ?
                """,
                (normalized_owner, normalized_key),
            ).fetchone()
            if updated is None:
                raise RuntimeError("failed creation reservation could not be loaded")
            return self._creation_record(updated)
