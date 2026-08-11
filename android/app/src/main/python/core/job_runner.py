"""Adapters that execute existing generation streams as durable jobs."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from .job_store import GenerationJobStore, JobStatus


log = logging.getLogger(__name__)


class ChapterJobRunner:
    """Run one chapter stream while persisting lifecycle state and cancellation."""

    PUBLIC_FAILURE_MESSAGE = "生成章节失败，请稍后重试"

    def __init__(self, engine: Any, job_store: GenerationJobStore):
        self.engine = engine
        self.job_store = job_store

    @staticmethod
    def _progress(event: dict) -> Optional[int]:
        value = event.get("pct")
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return max(0, min(99, int(value)))
        return None

    async def run_chapter(
        self,
        job_id: str,
        novel_id: str,
        chapter_num: int,
        writing_mode: str = "webnovel",
        feedback: Optional[str] = None,
    ) -> None:
        current = self.job_store.get(job_id)
        if current is None:
            raise KeyError(f"generation job not found: {job_id}")
        if current["status"] == JobStatus.CANCEL_REQUESTED:
            self.job_store.mark_cancelled(job_id)
            return
        if current["status"] in {
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }:
            return

        self.job_store.mark_running(job_id, phase="starting")
        saw_done = False
        try:
            async for event in self.engine.generate_chapter_stream(
                novel_id,
                chapter_num,
                writing_mode,
                feedback=feedback,
            ):
                if self.job_store.is_cancel_requested(job_id):
                    self.job_store.mark_cancelled(job_id)
                    return
                if not isinstance(event, dict):
                    self.job_store.fail(
                        job_id,
                        error_code="INVALID_EVENT",
                        error_message=self.PUBLIC_FAILURE_MESSAGE,
                    )
                    return

                event_type = event.get("type")
                if event_type == "error":
                    log.warning("Chapter job %s returned an engine error event", job_id)
                    self.job_store.fail(
                        job_id,
                        error_code="GENERATION_ERROR",
                        error_message=self.PUBLIC_FAILURE_MESSAGE,
                    )
                    return

                progress = self._progress(event)
                if progress is not None:
                    self.job_store.update_progress(
                        job_id,
                        progress=progress,
                        phase=str(event.get("phase") or event_type or "running")[:100],
                    )
                if event_type == "done":
                    saw_done = True

            if self.job_store.is_cancel_requested(job_id):
                self.job_store.mark_cancelled(job_id)
                return
            if not saw_done:
                self.job_store.fail(
                    job_id,
                    error_code="INCOMPLETE_RESULT",
                    error_message=self.PUBLIC_FAILURE_MESSAGE,
                )
                return
            self.job_store.complete(
                job_id,
                result={"novel_id": novel_id, "chapter_num": chapter_num},
            )
        except asyncio.CancelledError:
            try:
                if self.job_store.is_cancel_requested(job_id):
                    self.job_store.mark_cancelled(job_id)
                else:
                    self.job_store.fail(
                        job_id,
                        error_code="WORKER_CANCELLED",
                        error_message=self.PUBLIC_FAILURE_MESSAGE,
                    )
            finally:
                return
        except Exception:
            log.exception("Chapter job %s failed", job_id)
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
