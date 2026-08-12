"""Durable account consent, revocation, and deletion state."""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional


class AccountStore:
    def __init__(self, novels_dir: os.PathLike[str] | str, db_name: str = "accounts.db"):
        root = Path(novels_dir)
        root.mkdir(parents=True, exist_ok=True)
        self._path = root / db_name
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self._path), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA journal_mode = WAL")
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
        value = owner_id.strip()
        if not value or len(value) > 200:
            raise ValueError("owner_id must not be empty or too long")
        return value

    def _initialize(self) -> None:
        with self._lock, self._session() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    owner_id TEXT PRIMARY KEY,
                    consent_version TEXT,
                    consent_at REAL,
                    revoked_before REAL NOT NULL DEFAULT 0,
                    deleted_at REAL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )

    def ensure_user(self, owner_id: str) -> dict:
        owner = self._owner(owner_id)
        with self._lock, self._session() as connection:
            row = connection.execute(
                "SELECT * FROM accounts WHERE owner_id = ?", (owner,)
            ).fetchone()
            if row is None:
                now = time.time()
                connection.execute(
                    """
                    INSERT INTO accounts (owner_id, created_at, updated_at)
                    VALUES (?, ?, ?)
                    """,
                    (owner, now, now),
                )
                row = connection.execute(
                    "SELECT * FROM accounts WHERE owner_id = ?", (owner,)
                ).fetchone()
            return self._record(row)

    @staticmethod
    def _record(row: sqlite3.Row) -> dict:
        return {
            "owner_id": row["owner_id"],
            "consent_version": row["consent_version"],
            "consent_at": row["consent_at"],
            "revoked_before": row["revoked_before"],
            "deleted_at": row["deleted_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get(self, owner_id: str) -> Optional[dict]:
        owner = self._owner(owner_id)
        with self._lock, self._session() as connection:
            row = connection.execute(
                "SELECT * FROM accounts WHERE owner_id = ?", (owner,)
            ).fetchone()
            return self._record(row) if row is not None else None

    def record_consent(self, owner_id: str, policy_version: str) -> dict:
        owner = self._owner(owner_id)
        if not isinstance(policy_version, str) or not 1 <= len(policy_version.strip()) <= 100:
            raise ValueError("policy_version is invalid")
        version = policy_version.strip()
        self.ensure_user(owner)
        now = time.time()
        with self._lock, self._session() as connection:
            connection.execute(
                """
                UPDATE accounts
                SET consent_version = ?, consent_at = ?, updated_at = ?
                WHERE owner_id = ? AND deleted_at IS NULL
                """,
                (version, now, now, owner),
            )
            row = connection.execute(
                "SELECT * FROM accounts WHERE owner_id = ?", (owner,)
            ).fetchone()
            return self._record(row)

    def has_consent(self, owner_id: str, policy_version: str) -> bool:
        record = self.get(owner_id)
        return bool(
            record
            and record["deleted_at"] is None
            and record["consent_version"] == policy_version
            and record["consent_at"]
        )

    def is_revoked(self, owner_id: str, issued_at: int) -> bool:
        record = self.get(owner_id)
        if record is None or record["deleted_at"] is not None:
            return True
        return issued_at <= float(record["revoked_before"] or 0)

    def revoke_all(self, owner_id: str) -> dict:
        owner = self._owner(owner_id)
        self.ensure_user(owner)
        now = time.time()
        # JWT issued_at 当前按整数秒传递；向上取整，避免撤销恰好跨秒时
        # “刚撤销但当前 token 仍被认为有效”的边界竞态。
        revoked_before = int(now) + 1
        with self._lock, self._session() as connection:
            connection.execute(
                "UPDATE accounts SET revoked_before = ?, updated_at = ? WHERE owner_id = ?",
                (revoked_before, now, owner),
            )
            row = connection.execute(
                "SELECT * FROM accounts WHERE owner_id = ?", (owner,)
            ).fetchone()
            return self._record(row)

    def delete_account(self, owner_id: str) -> dict:
        owner = self._owner(owner_id)
        self.ensure_user(owner)
        now = time.time()
        revoked_before = int(now) + 1
        with self._lock, self._session() as connection:
            connection.execute(
                """
                UPDATE accounts
                SET deleted_at = ?, revoked_before = ?, updated_at = ?
                WHERE owner_id = ?
                """,
                (now, revoked_before, now, owner),
            )
            row = connection.execute(
                "SELECT * FROM accounts WHERE owner_id = ?", (owner,)
            ).fetchone()
            return self._record(row)
