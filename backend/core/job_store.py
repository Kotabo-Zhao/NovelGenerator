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
