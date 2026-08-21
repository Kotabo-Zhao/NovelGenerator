"""Persistent state for long-running generation jobs.

The store is deliberately independent from FastAPI and the LLM client.  That
keeps the job contract testable on a minimal Python installation and gives the
API/worker layers one place to implement retries, progress, and cancellation.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Mapping, Optional


class JobStatus:
    """Stable status values shared by the API and a future worker."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"


TERMINAL_JOB_STATUSES = frozenset(
    {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}
)


class JobCapacityExceeded(RuntimeError):
    """Raised when a new owner job would exceed the active-job limit."""

_ALLOWED_TRANSITIONS = {
    JobStatus.QUEUED: frozenset(
        {JobStatus.RUNNING, JobStatus.FAILED, JobStatus.CANCEL_REQUESTED, JobStatus.CANCELLED}
    ),
    JobStatus.RUNNING: frozenset(
        {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCEL_REQUESTED}
    ),
    JobStatus.CANCEL_REQUESTED: frozenset({JobStatus.CANCELLED, JobStatus.FAILED}),
    JobStatus.SUCCEEDED: frozenset(),
    JobStatus.FAILED: frozenset(),
    JobStatus.CANCELLED: frozenset(),
}


class GenerationJobStore:
    """SQLite-backed job state with idempotent creation and cooperative cancel."""

    def __init__(self, novels_dir: os.PathLike[str] | str, db_name: str = "generation_jobs.db"):
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
                CREATE TABLE IF NOT EXISTS generation_jobs (
                    job_id TEXT PRIMARY KEY,
                    idempotency_scope TEXT NOT NULL,
                    idempotency_key TEXT,
                    owner_id TEXT,
                    novel_id TEXT NOT NULL,
                    job_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0,
                    phase TEXT NOT NULL DEFAULT 'queued',
                    request_json TEXT NOT NULL,
                    result_json TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    started_at REAL,
                    finished_at REAL,
                    UNIQUE(idempotency_scope, idempotency_key)
                );
                CREATE INDEX IF NOT EXISTS idx_generation_jobs_status
                    ON generation_jobs(status, updated_at);
                """
            )

    @staticmethod
    def _require_text(value: str, field: str, max_length: int = 200) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{field} must be a string")
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{field} must not be empty")
        if len(normalized) > max_length:
            raise ValueError(f"{field} is too long")
        return normalized

    @classmethod
    def _serialize(cls, value: Any, field: str) -> str:
        try:
            return json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} must be JSON serializable") from exc

    @staticmethod
    def _deserialize(value: Optional[str]) -> Optional[Any]:
        if value is None:
            return None
        return json.loads(value)

    @classmethod
    def _row_to_dict(cls, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "job_id": row["job_id"],
            "owner_id": row["owner_id"],
            "novel_id": row["novel_id"],
            "job_type": row["job_type"],
            "status": row["status"],
            "progress": row["progress"],
            "phase": row["phase"],
            "request": cls._deserialize(row["request_json"]),
            "result": cls._deserialize(row["result_json"]),
            "error_code": row["error_code"],
            "error_message": row["error_message"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
        }

    @staticmethod
    def _fetch_one(connection: sqlite3.Connection, job_id: str) -> Optional[sqlite3.Row]:
        return connection.execute(
            "SELECT * FROM generation_jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()

    def create_or_get(
        self,
        *,
        job_type: str,
        novel_id: str,
        request: Mapping[str, Any],
        owner_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        max_active: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Create a queued job or return the job for a retried request.

        Until WeChat authentication is connected, ``owner_id`` remains
        optional and idempotency is scoped to ``anonymous``.  The field is
        persisted now so the authentication adapter can be added without
        changing the job schema or response contract.
        """

        normalized_type = self._require_text(job_type, "job_type")
        normalized_novel = self._require_text(novel_id, "novel_id")
        if not isinstance(request, Mapping):
            raise ValueError("request must be a mapping")
        request_json = self._serialize(dict(request), "request")
        normalized_owner = None
        if owner_id is not None:
            normalized_owner = self._require_text(owner_id, "owner_id")
        normalized_key = None
        if idempotency_key is not None:
            normalized_key = self._require_text(idempotency_key, "idempotency_key")
        if max_active is not None and (
            isinstance(max_active, bool) or not isinstance(max_active, int) or max_active < 1
        ):
            raise ValueError("max_active must be a positive integer")
        scope = normalized_owner or "anonymous"

        with self._lock, self._session() as connection:
            if normalized_key is not None:
                existing = connection.execute(
                    """
                    SELECT * FROM generation_jobs
                    WHERE idempotency_scope = ? AND idempotency_key = ?
                    """,
                    (scope, normalized_key),
                ).fetchone()
                if existing is not None:
                    if (
                        existing["job_type"] != normalized_type
                        or existing["novel_id"] != normalized_novel
                        or existing["request_json"] != request_json
                    ):
                        raise ValueError("idempotency_key is already bound to another request")
                    return self._row_to_dict(existing)

            if normalized_owner is not None and max_active is not None:
                active = connection.execute(
                    """
                    SELECT COUNT(*) AS count FROM generation_jobs
                    WHERE owner_id = ? AND status IN (?, ?, ?)
                    """,
                    (
                        normalized_owner,
                        JobStatus.QUEUED,
                        JobStatus.RUNNING,
                        JobStatus.CANCEL_REQUESTED,
                    ),
                ).fetchone()
                if active and int(active["count"]) >= max_active:
                    raise JobCapacityExceeded("active generation job limit reached")

            now = time.time()
            job_id = str(uuid.uuid4())
            try:
                connection.execute(
                    """
                    INSERT INTO generation_jobs (
                        job_id, idempotency_scope, idempotency_key, owner_id,
                        novel_id, job_type, status, progress, phase,
                        request_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        scope,
                        normalized_key,
                        normalized_owner,
                        normalized_novel,
                        normalized_type,
                        JobStatus.QUEUED,
                        JobStatus.QUEUED,
                        request_json,
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError:
                if normalized_key is None:
                    raise
                existing = connection.execute(
                    """
                    SELECT * FROM generation_jobs
                    WHERE idempotency_scope = ? AND idempotency_key = ?
                    """,
                    (scope, normalized_key),
                ).fetchone()
                if existing is None:
                    raise
                if (
                    existing["job_type"] != normalized_type
                    or existing["novel_id"] != normalized_novel
                    or existing["request_json"] != request_json
                ):
                    raise ValueError("idempotency_key is already bound to another request")
                return self._row_to_dict(existing)

            row = self._fetch_one(connection, job_id)
            if row is None:
                raise RuntimeError("created generation job could not be loaded")
            return self._row_to_dict(row)

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        normalized_id = self._require_text(job_id, "job_id", max_length=100)
        with self._lock, self._session() as connection:
            row = self._fetch_one(connection, normalized_id)
            return self._row_to_dict(row) if row is not None else None

    def _transition(
        self,
        job_id: str,
        target_status: str,
        *,
        phase: Optional[str] = None,
        progress: Optional[int] = None,
        result_json: Optional[str] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_id = self._require_text(job_id, "job_id", max_length=100)
        with self._lock, self._session() as connection:
            row = self._fetch_one(connection, normalized_id)
            if row is None:
                raise KeyError(f"generation job not found: {normalized_id}")
            current_status = row["status"]
            if target_status != current_status and target_status not in _ALLOWED_TRANSITIONS[current_status]:
                raise ValueError(
                    f"invalid job transition: {current_status} -> {target_status}"
                )

            now = time.time()
            next_phase = phase if phase is not None else row["phase"]
            next_progress = row["progress"] if progress is None else progress
            started_at = row["started_at"]
            finished_at = row["finished_at"]
            if target_status == JobStatus.RUNNING and started_at is None:
                started_at = now
            if target_status in TERMINAL_JOB_STATUSES:
                finished_at = now
            if target_status == JobStatus.SUCCEEDED:
                next_progress = 100

            connection.execute(
                """
                UPDATE generation_jobs
                SET status = ?, progress = ?, phase = ?, result_json = ?,
                    error_code = ?, error_message = ?, updated_at = ?,
                    started_at = ?, finished_at = ?
                WHERE job_id = ?
                """,
                (
                    target_status,
                    next_progress,
                    next_phase,
                    result_json if result_json is not None else row["result_json"],
                    error_code if error_code is not None else row["error_code"],
                    error_message if error_message is not None else row["error_message"],
                    now,
                    started_at,
                    finished_at,
                    normalized_id,
                ),
            )
            updated = self._fetch_one(connection, normalized_id)
            if updated is None:
                raise RuntimeError("updated generation job could not be loaded")
            return self._row_to_dict(updated)

    def mark_running(self, job_id: str, *, phase: str = "running") -> Dict[str, Any]:
        return self._transition(
            job_id,
            JobStatus.RUNNING,
            phase=self._require_text(phase, "phase", max_length=100),
        )

    def try_claim_running(self, job_id: str, *, phase: str = "running") -> Optional[Dict[str, Any]]:
        """Atomically claim a queued job for one worker.

        ``mark_running`` is intentionally kept for administrative callers, but
        workers must use this method so two API processes cannot execute the
        same queued job after a retry or restart.
        """

        normalized_id = self._require_text(job_id, "job_id", max_length=100)
        normalized_phase = self._require_text(phase, "phase", max_length=100)
        now = time.time()
        with self._lock, self._session() as connection:
            changed = connection.execute(
                """
                UPDATE generation_jobs
                SET status = ?, phase = ?, updated_at = ?, started_at = COALESCE(started_at, ?)
                WHERE job_id = ? AND status = ?
                """,
                (
                    JobStatus.RUNNING,
                    normalized_phase,
                    now,
                    now,
                    normalized_id,
                    JobStatus.QUEUED,
                ),
            ).rowcount
            if changed != 1:
                return None
            row = self._fetch_one(connection, normalized_id)
            return self._row_to_dict(row) if row is not None else None

    def list_recoverable(self, *, limit: int = 100) -> list[Dict[str, Any]]:
        """Return queued jobs that can be handed to a worker after restart."""

        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer between 1 and 1000")
        with self._lock, self._session() as connection:
            rows = connection.execute(
                """
                SELECT * FROM generation_jobs
                WHERE status = ?
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (JobStatus.QUEUED, limit),
            ).fetchall()
            return [self._row_to_dict(row) for row in rows]

    def list_for_owner(self, owner_id: str, *, active_only: bool = False) -> list[Dict[str, Any]]:
        normalized_owner = self._require_text(owner_id, "owner_id")
        query = "SELECT * FROM generation_jobs WHERE owner_id = ?"
        params: list[Any] = [normalized_owner]
        if active_only:
            query += " AND status IN (?, ?, ?)"
            params.extend(
                [JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.CANCEL_REQUESTED]
            )
        query += " ORDER BY created_at ASC"
        with self._lock, self._session() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
            return [self._row_to_dict(row) for row in rows]

    def requeue_stale(self, *, stale_after_seconds: float = 900) -> Dict[str, int]:
        """Recover workers that disappeared without completing their job.

        A cancelled worker is terminalized, while an abandoned running job is
        returned to the queue.  The next worker still has to atomically claim it.
        """

        if isinstance(stale_after_seconds, bool) or stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive")
        cutoff = time.time() - float(stale_after_seconds)
        now = time.time()
        with self._lock, self._session() as connection:
            requeued = connection.execute(
                """
                UPDATE generation_jobs
                SET status = ?, phase = ?, updated_at = ?, finished_at = NULL
                WHERE status = ? AND updated_at < ?
                """,
                (JobStatus.QUEUED, "recovered", now, JobStatus.RUNNING, cutoff),
            ).rowcount
            cancelled = connection.execute(
                """
                UPDATE generation_jobs
                SET status = ?, phase = ?, error_code = ?, error_message = ?,
                    updated_at = ?, finished_at = COALESCE(finished_at, ?)
                WHERE status = ? AND updated_at < ?
                """,
                (
                    JobStatus.CANCELLED,
                    "cancelled",
                    "CANCELLED",
                    "worker stopped after cancellation request",
                    now,
                    now,
                    JobStatus.CANCEL_REQUESTED,
                    cutoff,
                ),
            ).rowcount
        return {"requeued": requeued, "cancelled": cancelled}

    def count_active(self, owner_id: str) -> int:
        """Count queued/running jobs for concurrency admission control."""

        normalized_owner = self._require_text(owner_id, "owner_id")
        with self._lock, self._session() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count FROM generation_jobs
                WHERE owner_id = ? AND status IN (?, ?, ?)
                """,
                (
                    normalized_owner,
                    JobStatus.QUEUED,
                    JobStatus.RUNNING,
                    JobStatus.CANCEL_REQUESTED,
                ),
            ).fetchone()
            return int(row["count"] if row else 0)

    def purge_owner(self, owner_id: str) -> int:
        """Delete retained task metadata as part of account erasure."""

        normalized_owner = self._require_text(owner_id, "owner_id")
        with self._lock, self._session() as connection:
            result = connection.execute(
                "DELETE FROM generation_jobs WHERE owner_id = ?",
                (normalized_owner,),
            )
            return int(result.rowcount)

    def update_progress(self, job_id: str, *, progress: int, phase: str) -> Dict[str, Any]:
        if isinstance(progress, bool) or not isinstance(progress, int):
            raise ValueError("progress must be an integer")
        if not 0 <= progress <= 100:
            raise ValueError("progress must be between 0 and 100")
        normalized_phase = self._require_text(phase, "phase", max_length=100)
        with self._lock:
            current = self.get(job_id)
            if current is None:
                raise KeyError(f"generation job not found: {job_id}")
            if current["status"] not in {JobStatus.QUEUED, JobStatus.RUNNING}:
                raise ValueError("progress can only be updated for queued or running jobs")
            return self._transition(
                job_id,
                current["status"],
                phase=normalized_phase,
                progress=progress,
            )

    def touch(self, job_id: str) -> Dict[str, Any]:
        """Refresh updated_at without changing state — worker heartbeat.

        Long chapter generations emit no progress events for minutes at a
        time; without a heartbeat ``requeue_stale`` would re-queue an alive
        job past the stale threshold, causing duplicate LLM work and double
        billing.  Workers should call this at least every
        ``JOB_RECOVERY_STALE_SECONDS / 3`` seconds.
        """
        normalized_id = self._require_text(job_id, "job_id", max_length=100)
        with self._lock, self._session() as connection:
            row = self._fetch_one(connection, normalized_id)
            if row is None:
                raise KeyError(f"generation job not found: {normalized_id}")
            now = time.time()
            connection.execute(
                "UPDATE generation_jobs SET updated_at = ? WHERE job_id = ?",
                (now, normalized_id),
            )
            updated = self._fetch_one(connection, normalized_id)
            if updated is None:
                raise RuntimeError("touched generation job could not be loaded")
            return self._row_to_dict(updated)

    def complete(self, job_id: str, *, result: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        result_json = self._serialize(dict(result or {}), "result")
        return self._transition(
            job_id,
            JobStatus.SUCCEEDED,
            phase="completed",
            result_json=result_json,
            error_code="",
            error_message="",
        )

    def fail(self, job_id: str, *, error_code: str, error_message: str) -> Dict[str, Any]:
        return self._transition(
            job_id,
            JobStatus.FAILED,
            phase="failed",
            error_code=self._require_text(error_code, "error_code", max_length=100),
            error_message=self._require_text(error_message, "error_message", max_length=2000),
        )

    def request_cancel(self, job_id: str) -> Dict[str, Any]:
        current = self.get(job_id)
        if current is None:
            raise KeyError(f"generation job not found: {job_id}")
        if current["status"] in TERMINAL_JOB_STATUSES:
            return current
        if current["status"] == JobStatus.QUEUED:
            return self._transition(
                job_id,
                JobStatus.CANCELLED,
                phase="cancelled",
                error_code="CANCELLED",
                error_message="job cancelled before start",
            )
        if current["status"] == JobStatus.CANCEL_REQUESTED:
            return current
        return self._transition(job_id, JobStatus.CANCEL_REQUESTED, phase="cancelling")

    def is_cancel_requested(self, job_id: str) -> bool:
        current = self.get(job_id)
        if current is None:
            raise KeyError(f"generation job not found: {job_id}")
        return current["status"] == JobStatus.CANCEL_REQUESTED

    def mark_cancelled(self, job_id: str) -> Dict[str, Any]:
        current = self.get(job_id)
        if current is None:
            raise KeyError(f"generation job not found: {job_id}")
        if current["status"] == JobStatus.CANCELLED:
            return current
        return self._transition(
            job_id,
            JobStatus.CANCELLED,
            phase="cancelled",
            error_code="CANCELLED",
            error_message="job cancelled",
        )
