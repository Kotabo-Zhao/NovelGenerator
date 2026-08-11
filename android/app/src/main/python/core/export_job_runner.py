"""Durable file exports for authenticated clients."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from .job_store import GenerationJobStore, JobStatus


log = logging.getLogger(__name__)


class ExportJobRunner:
    """Run the existing synchronous exporter without blocking the event loop."""

    PUBLIC_FAILURE_MESSAGE = "导出失败，请稍后重试"

    def __init__(
        self,
        engine: Any,
        job_store: GenerationJobStore,
        output_root: os.PathLike[str] | str,
        *,
        timeout_seconds: Optional[float] = 300,
        retention_seconds: int = 24 * 3600,
    ):
        self.engine = engine
        self.job_store = job_store
        self.output_root = Path(output_root).resolve()
        self.timeout_seconds = timeout_seconds
        self.retention_seconds = retention_seconds

    @staticmethod
    def _owner_dir(owner_id: str) -> str:
        return hashlib.sha256(owner_id.encode("utf-8")).hexdigest()[:32]

    def output_path(self, owner_id: str, job_id: str, fmt: str) -> Path:
        path = self.output_root / self._owner_dir(owner_id) / f"{uuid.UUID(job_id)}.{fmt}"
        resolved = path.resolve()
        if resolved.parent.parent != self.output_root:
            raise ValueError("export path escaped output root")
        return resolved

    async def run_export(self, job_id: str, owner_id: str, novel_id: str, fmt: str) -> None:
        claimed = self.job_store.try_claim_running(job_id, phase="exporting")
        if claimed is None:
            return
        try:
            call = asyncio.to_thread(self.engine.export_novel, novel_id, fmt)
            if self.timeout_seconds is not None:
                content, error = await asyncio.wait_for(call, timeout=self.timeout_seconds)
            else:
                content, error = await call
            if error:
                self.job_store.fail(
                    job_id,
                    error_code="EXPORT_ERROR",
                    error_message=self.PUBLIC_FAILURE_MESSAGE,
                )
                return
            if isinstance(content, str):
                payload = content.encode("utf-8")
            elif isinstance(content, bytes):
                payload = content
            else:
                raise TypeError("exporter returned an unsupported payload")

            destination = self.output_path(owner_id, job_id, fmt)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
            try:
                temporary.write_bytes(payload)
                os.replace(temporary, destination)
            finally:
                if temporary.exists():
                    temporary.unlink()
            expires_at = time.time() + self.retention_seconds
            self.job_store.complete(
                job_id,
                result={
                    "filename": f"{novel_id}.{fmt}",
                    "path": str(destination),
                    "size_bytes": len(payload),
                    "expires_at": expires_at,
                },
            )
        except asyncio.TimeoutError:
            log.warning("Export job %s timed out", job_id)
            self.job_store.fail(
                job_id,
                error_code="TIMEOUT",
                error_message=self.PUBLIC_FAILURE_MESSAGE,
            )
        except asyncio.CancelledError:
            current = self.job_store.get(job_id)
            if current and current["status"] == JobStatus.CANCEL_REQUESTED:
                self.job_store.mark_cancelled(job_id)
            else:
                self.job_store.fail(
                    job_id,
                    error_code="WORKER_CANCELLED",
                    error_message=self.PUBLIC_FAILURE_MESSAGE,
                )
        except Exception:
            log.exception("Export job %s failed", job_id)
            current = self.job_store.get(job_id)
            if current and current["status"] == JobStatus.CANCEL_REQUESTED:
                self.job_store.mark_cancelled(job_id)
            elif current and current["status"] not in {
                JobStatus.SUCCEEDED,
                JobStatus.FAILED,
                JobStatus.CANCELLED,
            }:
                self.job_store.fail(
                    job_id,
                    error_code="WORKER_ERROR",
                    error_message=self.PUBLIC_FAILURE_MESSAGE,
                )
