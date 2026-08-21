"""Adapters that execute existing generation streams as durable jobs."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from .job_store import GenerationJobStore, JobStatus
from .usage_context import usage_scope


log = logging.getLogger(__name__)


class ChapterJobRunner:
    """Run one chapter stream while persisting lifecycle state and cancellation."""

    PUBLIC_FAILURE_MESSAGE = "生成章节失败，请稍后重试"
    # 长章节生成可能数分钟不产出 progress 事件；心跳间隔应远小于
    # JOB_RECOVERY_STALE_SECONDS（默认 900s），避免 requeue_stale 把存活任务回队。
    HEARTBEAT_INTERVAL_SECONDS = 30.0

    def __init__(
        self,
        engine: Any,
        job_store: GenerationJobStore,
        *,
        timeout_seconds: Optional[float] = None,
        usage_store: Any = None,
    ):
        self.engine = engine
        self.job_store = job_store
        self.timeout_seconds = timeout_seconds
        self.usage_store = usage_store

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

        # Only a queued row can be claimed.  A second process that observes a
        # running row must leave it alone instead of starting a duplicate LLM
        # request.
        claimed = self.job_store.try_claim_running(job_id, phase="starting")
        if claimed is None:
            return

        usage_events = []
        try:
            with usage_scope(usage_events.append):
                if self.timeout_seconds is not None:
                    await asyncio.wait_for(
                        self._consume_stream(
                            job_id,
                            novel_id,
                            chapter_num,
                            writing_mode,
                            feedback,
                        ),
                        timeout=self.timeout_seconds,
                    )
                else:
                    await self._consume_stream(
                        job_id,
                        novel_id,
                        chapter_num,
                        writing_mode,
                        feedback,
                    )
        except asyncio.TimeoutError:
            log.warning("Chapter job %s timed out after %ss", job_id, self.timeout_seconds)
            current = self.job_store.get(job_id)
            if current and current["status"] not in {
                JobStatus.SUCCEEDED,
                JobStatus.FAILED,
                JobStatus.CANCELLED,
            }:
                self.job_store.fail(
                    job_id,
                    error_code="TIMEOUT",
                    error_message=self.PUBLIC_FAILURE_MESSAGE,
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
        finally:
            # current 可能在异常分支被重新赋值；防御性取值避免掩盖原始异常
            self._persist_usage((current or {}).get("owner_id", ""), job_id, usage_events)

    def _persist_usage(self, owner_id: str, job_id: str, events: list[dict]) -> None:
        if self.usage_store is None or not events or not owner_id:
            return
        input_tokens = sum(max(0, int(event.get("input_tokens", 0) or 0)) for event in events)
        output_tokens = sum(max(0, int(event.get("output_tokens", 0) or 0)) for event in events)
        cost_micros = sum(max(0, int(event.get("cost_micros", 0) or 0)) for event in events)
        model = next((str(event.get("model")) for event in events if event.get("model")), "unknown")
        try:
            self.usage_store.record_usage(
                owner_id=owner_id,
                job_id=job_id,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_micros=cost_micros,
            )
        except Exception:
            log.exception("Failed to persist usage for job %s", job_id)

    async def _consume_stream(
        self,
        job_id: str,
        novel_id: str,
        chapter_num: int,
        writing_mode: str,
        feedback: Optional[str],
    ) -> None:
        saw_done = False
        saw_needs_revision = False
        last_heartbeat = time.monotonic()
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
            if event_type == "needs_revision":
                # v2.8.5: 引擎在"可重试的修订失败"时 yield needs_revision 后 return，
                # 不再 yield done。此前被误判为 INCOMPLETE_RESULT 任务失败，
                # 错误码语义丢失。落 FAILED 但带显式 NEEDS_REVISION 码。
                saw_needs_revision = True

            progress = self._progress(event)
            if progress is not None:
                self.job_store.update_progress(
                    job_id,
                    progress=progress,
                    phase=str(event.get("phase") or event_type or "running")[:100],
                )
                last_heartbeat = time.monotonic()
            elif time.monotonic() - last_heartbeat >= self.HEARTBEAT_INTERVAL_SECONDS:
                # 引擎长静默期（无 progress 事件）也要刷新 updated_at，
                # 否则 requeue_stale 会把存活任务回队造成重复生成/双份计费。
                try:
                    self.job_store.touch(job_id)
                except (KeyError, ValueError):
                    log.exception("Heartbeat failed for job %s", job_id)
                    return
                last_heartbeat = time.monotonic()
            if event_type == "done":
                saw_done = True

        if self.job_store.is_cancel_requested(job_id):
            self.job_store.mark_cancelled(job_id)
            return
        if saw_needs_revision and not saw_done:
            self.job_store.fail(
                job_id,
                error_code="NEEDS_REVISION",
                error_message="本章未通过质量校验，草稿已保留，请修改后重新生成",
            )
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
