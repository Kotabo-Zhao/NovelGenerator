"""Durable daily usage reservations for authenticated generation jobs."""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Optional


class QuotaExceeded(RuntimeError):
    """Raised when a user reaches a configured daily generation budget."""

    def __init__(self, message: str, *, retry_after: int = 3600):
        super().__init__(message)
        self.retry_after = retry_after


class UsageQuotaStore:
    """SQLite-backed reservations that survive API restarts."""

    def __init__(self, novels_dir: os.PathLike[str] | str, db_name: str = "usage_quota.db"):
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

    def _initialize(self) -> None:
        with self._lock, self._session() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS usage_daily (
                    owner_id TEXT NOT NULL,
                    usage_day TEXT NOT NULL,
                    jobs_reserved INTEGER NOT NULL DEFAULT 0,
                    tokens_reserved INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(owner_id, usage_day)
                );
                CREATE TABLE IF NOT EXISTS usage_reservations (
                    owner_id TEXT NOT NULL,
                    reservation_key TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    usage_day TEXT NOT NULL,
                    token_budget INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(owner_id, reservation_key)
                );
                """
            )

    @staticmethod
    def _text(value: str, field: str, max_length: int = 200) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{field} must be a string")
        normalized = value.strip()
        if not normalized or len(normalized) > max_length:
            raise ValueError(f"{field} is empty or too long")
        return normalized

    @staticmethod
    def _hash(value: str) -> str:
        normalized = UsageQuotaStore._text(value, "request_hash", 128).lower()
        if len(normalized) != 64:
            raise ValueError("request_hash must be a SHA-256 hex digest")
        try:
            int(normalized, 16)
        except ValueError as exc:
            raise ValueError("request_hash must be a SHA-256 hex digest") from exc
        return normalized

    @staticmethod
    def _day() -> str:
        return time.strftime("%Y-%m-%d", time.gmtime())

    def reserve(
        self,
        *,
        owner_id: str,
        reservation_key: str,
        request_hash: str,
        token_budget: int,
        daily_job_limit: int,
        daily_token_limit: int,
    ) -> Dict[str, object]:
        """Reserve one idempotent generation attempt or raise ``QuotaExceeded``."""
        owner = self._text(owner_id, "owner_id")
        key = self._text(reservation_key, "reservation_key")
        digest = self._hash(request_hash)
        if not isinstance(token_budget, int) or token_budget < 1:
            raise ValueError("token_budget must be positive")
        if not isinstance(daily_job_limit, int) or daily_job_limit < 1:
            raise ValueError("daily_job_limit must be positive")
        if not isinstance(daily_token_limit, int) or daily_token_limit < token_budget:
            raise ValueError("daily_token_limit must cover token_budget")

        day = self._day()
        with self._lock, self._session() as connection:
            existing = connection.execute(
                """
                SELECT * FROM usage_reservations
                WHERE owner_id = ? AND reservation_key = ?
                """,
                (owner, key),
            ).fetchone()
            if existing is not None:
                if existing["request_hash"] != digest:
                    raise ValueError("reservation_key is already bound to another request")
                return {
                    "replayed": True,
                    "usage_day": existing["usage_day"],
                    "token_budget": existing["token_budget"],
                }

            usage = connection.execute(
                """
                SELECT jobs_reserved, tokens_reserved FROM usage_daily
                WHERE owner_id = ? AND usage_day = ?
                """,
                (owner, day),
            ).fetchone()
            jobs_used = usage["jobs_reserved"] if usage else 0
            tokens_used = usage["tokens_reserved"] if usage else 0
            if jobs_used >= daily_job_limit:
                raise QuotaExceeded("今日生成次数已用尽", retry_after=self._seconds_until_next_day())
            if tokens_used + token_budget > daily_token_limit:
                raise QuotaExceeded("今日生成额度已用尽", retry_after=self._seconds_until_next_day())

            now = time.time()
            if usage is None:
                connection.execute(
                    """
                    INSERT INTO usage_daily
                        (owner_id, usage_day, jobs_reserved, tokens_reserved, updated_at)
                    VALUES (?, ?, 1, ?, ?)
                    """,
                    (owner, day, token_budget, now),
                )
            else:
                connection.execute(
                    """
                    UPDATE usage_daily
                    SET jobs_reserved = jobs_reserved + 1,
                        tokens_reserved = tokens_reserved + ?, updated_at = ?
                    WHERE owner_id = ? AND usage_day = ?
                    """,
                    (token_budget, now, owner, day),
                )
            connection.execute(
                """
                INSERT INTO usage_reservations
                    (owner_id, reservation_key, request_hash, usage_day, token_budget, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (owner, key, digest, day, token_budget, now),
            )
            return {"replayed": False, "usage_day": day, "token_budget": token_budget}

    def release(self, *, owner_id: str, reservation_key: str, request_hash: str) -> bool:
        """Roll back a reservation when the job row could not be created."""
        owner = self._text(owner_id, "owner_id")
        key = self._text(reservation_key, "reservation_key")
        digest = self._hash(request_hash)
        with self._lock, self._session() as connection:
            row = connection.execute(
                """
                SELECT * FROM usage_reservations
                WHERE owner_id = ? AND reservation_key = ?
                """,
                (owner, key),
            ).fetchone()
            if row is None:
                return False
            if row["request_hash"] != digest:
                raise ValueError("reservation_key is already bound to another request")
            connection.execute(
                """
                DELETE FROM usage_reservations
                WHERE owner_id = ? AND reservation_key = ?
                """,
                (owner, key),
            )
            connection.execute(
                """
                UPDATE usage_daily
                SET jobs_reserved = MAX(0, jobs_reserved - 1),
                    tokens_reserved = MAX(0, tokens_reserved - ?), updated_at = ?
                WHERE owner_id = ? AND usage_day = ?
                """,
                (row["token_budget"], time.time(), owner, row["usage_day"]),
            )
            return True

    def get_usage(self, owner_id: str, usage_day: Optional[str] = None) -> Dict[str, int | str]:
        owner = self._text(owner_id, "owner_id")
        day = usage_day or self._day()
        with self._lock, self._session() as connection:
            row = connection.execute(
                """
                SELECT jobs_reserved, tokens_reserved FROM usage_daily
                WHERE owner_id = ? AND usage_day = ?
                """,
                (owner, day),
            ).fetchone()
        return {
            "usage_day": day,
            "jobs_reserved": row["jobs_reserved"] if row else 0,
            "tokens_reserved": row["tokens_reserved"] if row else 0,
        }

    @staticmethod
    def _seconds_until_next_day() -> int:
        return max(60, 86400 - int(time.time()) % 86400)
