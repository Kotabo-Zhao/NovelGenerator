"""Authenticated, task-oriented API for the Mini Program migration path."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Dict, Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from core.job_runner import ChapterJobRunner
from core.export_job_runner import ExportJobRunner
from core.job_store import JobCapacityExceeded, JobStatus
from core.path_security import validate_novel_id
from core.usage_quota import QuotaExceeded
from config import MAX_CHAPTER_TOKENS, NOVELS_DIR

from ..auth import require_consented_owner
from ..deps import engine, job_store, log, ownership_store, quota_store
from .novels import CreateNovelRequest


router = APIRouter(prefix="/v1", tags=["authenticated-v1"])
_active_tasks: Dict[str, asyncio.Task] = {}
DAILY_CHAPTER_JOB_LIMIT = int(os.getenv("DAILY_CHAPTER_JOB_LIMIT", "20"))
DAILY_TOKEN_LIMIT = int(os.getenv("DAILY_TOKEN_LIMIT", "80000"))
MAX_ACTIVE_JOBS_PER_OWNER = int(os.getenv("MAX_ACTIVE_JOBS_PER_OWNER", "2"))
GENERATION_TIMEOUT_SECONDS = float(os.getenv("GENERATION_TIMEOUT_SECONDS", "900"))
JOB_RECOVERY_STALE_SECONDS = float(os.getenv("JOB_RECOVERY_STALE_SECONDS", "900"))
EXPORT_RETENTION_SECONDS = int(os.getenv("EXPORT_RETENTION_SECONDS", str(24 * 3600)))
EXPORT_DIR = Path(NOVELS_DIR) / ".exports"


class ChapterJobRequest(BaseModel):
    chapter_num: int = Field(..., ge=1, le=100000)
    writing_mode: str = Field("webnovel", pattern="^(webnovel|literary)$")
    feedback: Optional[str] = Field(None, max_length=5000)


class ExportJobRequest(BaseModel):
    fmt: Literal["txt", "epub"] = "txt"


class ChapterDraftRequest(BaseModel):
    content: str = Field(..., max_length=50_000)


def _public_job(job: dict) -> dict:
    """Return only client-safe task fields; owner_id never crosses this boundary."""

    ownership = None
    try:
        ownership = ownership_store.require_owner(job["novel_id"], job["owner_id"])
    except (KeyError, ValueError, PermissionError):
        pass
    public_novel_id = (ownership or {}).get("public_id", job["novel_id"])
    result = job["result"]
    if isinstance(result, dict) and "novel_id" in result:
        result = {**result, "novel_id": public_novel_id}
    if job["job_type"] == "export" and isinstance(result, dict):
        result = {
            key: value
            for key, value in result.items()
            if key not in {"path"}
        }
        result["download_url"] = f"/v1/exports/{job['job_id']}/download"
    return {
        "job_id": job["job_id"],
        "novel_id": public_novel_id,
        "job_type": job["job_type"],
        "status": job["status"],
        "progress": job["progress"],
        "phase": job["phase"],
        "result": result,
        "error_code": job["error_code"],
        "error_message": job["error_message"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
        "started_at": job["started_at"],
        "finished_at": job["finished_at"],
    }


def _owned_record(novel_id: str, owner_id: str) -> dict:
    try:
        return ownership_store.require_owner(validate_novel_id(novel_id), owner_id)
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=404, detail="作品不存在") from exc


def _owned_storage_id(novel_id: str, owner_id: str) -> str:
    return str(_owned_record(novel_id, owner_id)["novel_id"])


def _owned_novel(novel_id: str, owner_id: str) -> dict:
    record = _owned_record(novel_id, owner_id)
    plan = engine.get_novel(record["novel_id"])
    if not plan:
        raise HTTPException(status_code=404, detail="作品不存在")
    plan = dict(plan)
    plan["public_id"] = record["public_id"]
    return plan


def _owned_job(job_id: str, owner_id: str) -> dict:
    try:
        normalized_job_id = str(uuid.UUID(job_id))
    except (ValueError, AttributeError, TypeError) as exc:
        raise HTTPException(status_code=404, detail="任务不存在") from exc
    job = job_store.get(normalized_job_id)
    if job is None or job.get("owner_id") != owner_id:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


def _start_chapter_task(job: dict, request: ChapterJobRequest) -> None:
    job_id = job["job_id"]
    existing = _active_tasks.get(job_id)
    if existing and not existing.done():
        return
    if job["status"] != "queued":
        return

    runner = ChapterJobRunner(
        engine,
        job_store,
        timeout_seconds=GENERATION_TIMEOUT_SECONDS,
        usage_store=quota_store,
    )
    task = asyncio.create_task(
        runner.run_chapter(
            job_id,
            job["novel_id"],
            request.chapter_num,
            request.writing_mode,
            request.feedback,
        )
    )
    _active_tasks[job_id] = task

    def _cleanup(done_task: asyncio.Task) -> None:
        _active_tasks.pop(job_id, None)
        if not done_task.cancelled() and done_task.exception():
            log.error("chapter job task crashed: %s", job_id)

    task.add_done_callback(_cleanup)


def _start_export_task(job: dict, request: ExportJobRequest) -> None:
    job_id = job["job_id"]
    existing = _active_tasks.get(job_id)
    if existing and not existing.done():
        return
    if job["status"] != "queued":
        return
    runner = ExportJobRunner(
        engine,
        job_store,
        EXPORT_DIR,
        timeout_seconds=GENERATION_TIMEOUT_SECONDS,
        retention_seconds=EXPORT_RETENTION_SECONDS,
    )
    task = asyncio.create_task(
        runner.run_export(job_id, job["owner_id"], job["novel_id"], request.fmt)
    )
    _active_tasks[job_id] = task

    def _cleanup(done_task: asyncio.Task) -> None:
        _active_tasks.pop(job_id, None)
        if not done_task.cancelled() and done_task.exception():
            log.error("export job task crashed: %s", job_id)

    task.add_done_callback(_cleanup)


async def recover_pending_jobs() -> dict:
    """Requeue abandoned work and resume queued chapter jobs at startup."""

    recovered = job_store.requeue_stale(
        stale_after_seconds=JOB_RECOVERY_STALE_SECONDS,
    )
    scheduled = 0
    invalid = 0
    for job in job_store.list_recoverable():
        try:
            if job.get("job_type") == "chapter":
                request = ChapterJobRequest(**(job.get("request") or {}))
                _start_chapter_task(job, request)
            elif job.get("job_type") == "export":
                request = ExportJobRequest(**(job.get("request") or {}))
                _start_export_task(job, request)
            else:
                continue
        except Exception:
            invalid += 1
            job_store.fail(
                job["job_id"],
                error_code="INVALID_REQUEST",
                error_message=ChapterJobRunner.PUBLIC_FAILURE_MESSAGE,
            )
            continue
        scheduled += 1
    if recovered["requeued"] or recovered["cancelled"] or scheduled or invalid:
        log.info(
            "Job recovery: requeued=%s cancelled=%s scheduled=%s invalid=%s",
            recovered["requeued"],
            recovered["cancelled"],
            scheduled,
            invalid,
        )
    return {**recovered, "scheduled": scheduled, "invalid": invalid}


# ── v3.8: 周期任务恢复（此前 requeue_stale 只在启动时执行一次）──

_periodic_recovery_task: Optional[asyncio.Task] = None


async def _periodic_recovery_loop() -> None:
    """周期 requeue 卡死的 RUNNING 任务。

    进程存活期间若 asyncio task 异常消亡（或任务在某处卡住未置终态），
    这些任务会永久占用 max_active 名额，用户无法再提交新任务。
    每 JOB_RECOVERY_STALE_SECONDS 秒扫描一次，恢复后重新调度。
    """
    while True:
        await asyncio.sleep(JOB_RECOVERY_STALE_SECONDS)
        try:
            await recover_pending_jobs()
        except Exception:
            log.exception("Periodic job recovery failed")


def start_periodic_recovery() -> None:
    """启动周期任务恢复（幂等，可被 startup 重复调用）。"""
    global _periodic_recovery_task
    t = _periodic_recovery_task
    if t is not None:
        try:
            if not t.done():
                return
        except Exception:
            pass  # 旧任务所在 loop 已关闭等异常——视为失效，重新创建
    _periodic_recovery_task = asyncio.create_task(_periodic_recovery_loop())


@router.get("/novels")
async def list_owned_novels(owner_id: str = Depends(require_consented_owner)):
    result = []
    for record in ownership_store.list_for_owner(owner_id):
        plan = engine.get_novel(record["novel_id"])
        if not plan:
            continue
        state = engine.memory.get_novel_state(record["novel_id"])
        result.append(
            {
                "id": record["public_id"],
                "public_id": record["public_id"],
                "title": plan.get("title", record["novel_id"]),
                "genre": plan.get("genre", ""),
                "style": plan.get("style", ""),
                "state": state,
                # 与 legacy engine.list_novels() 保持一致：小程序书架用它算完成进度
                "disk_chapters": len((state or {}).get("completed_chapters", [])),
            }
        )
    return {"novels": result}


@router.get("/usage")
async def get_usage(owner_id: str = Depends(require_consented_owner)):
    """Expose reservation and provider-reported usage separately."""
    return quota_store.get_usage(owner_id)


@router.post("/novels", status_code=status.HTTP_201_CREATED)
async def create_owned_novel(
    req: CreateNovelRequest,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    owner_id: str = Depends(require_consented_owner),
):
    """Create a novel once, then bind its server-generated title to the owner."""

    creative_input = {
        "genre": req.genre,
        "style": req.style,
        "inspiration": req.inspiration,
        "target_words": req.target_words,
        "title": req.title,
        "natural_names": req.natural_names,
        "normal_pacing": req.normal_pacing,
        "fast_food": req.fast_food,
        "creative_enhance": req.creative_enhance,
    }
    if not idempotency_key or not idempotency_key.strip():
        raise HTTPException(status_code=400, detail="缺少 Idempotency-Key")
    request_hash = hashlib.sha256(
        json.dumps(creative_input, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    try:
        reservation = ownership_store.reserve_creation(owner_id, idempotency_key, request_hash)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="幂等键已绑定其他创建请求") from exc
    if reservation.get("status") == "succeeded":
        replayed_plan = engine.get_novel(reservation["novel_id"])
        if replayed_plan:
            replayed_record = ownership_store.require_owner(reservation["novel_id"], owner_id)
            return {
                "novel": replayed_plan,
                "novel_id": reservation["novel_id"],
                "public_id": replayed_record["public_id"],
                "replayed": True,
            }
        raise HTTPException(status_code=409, detail="创建任务结果已失效，请重新提交")
    if reservation.get("replayed"):
        raise HTTPException(status_code=409, detail="相同创建请求正在处理中")
    try:
        plan = await asyncio.to_thread(engine.create_novel, creative_input)
        novel_id = validate_novel_id(plan.get("title", ""))
        ownership = ownership_store.claim(novel_id, owner_id)
        ownership_store.complete_creation(owner_id, idempotency_key, request_hash, novel_id)
    except (ValueError, PermissionError) as exc:
        ownership_store.fail_creation(owner_id, idempotency_key, request_hash, "作品创建结果无法绑定当前用户")
        raise HTTPException(status_code=409, detail="作品创建结果无法绑定当前用户") from exc
    except Exception as exc:
        ownership_store.fail_creation(owner_id, idempotency_key, request_hash, "作品创建失败")
        log.exception("Authenticated novel creation failed")
        raise HTTPException(status_code=500, detail="作品创建失败，请稍后重试") from exc
    return {
        "novel": plan,
        "novel_id": novel_id,
        "public_id": ownership["public_id"],
        "replayed": False,
    }


@router.get("/novels/{novel_id}")
async def get_owned_novel(novel_id: str, owner_id: str = Depends(require_consented_owner)):
    plan = _owned_novel(novel_id, owner_id)
    plan.pop("chapters", None)
    return {"novel": plan}


@router.get("/novels/{novel_id}/chapters/{chapter_num}")
async def get_owned_chapter(
    novel_id: str,
    chapter_num: int,
    offset: int = Query(0, ge=0, le=2_000_000),
    limit: int = Query(30_000, ge=1_000, le=50_000),
    owner_id: str = Depends(require_consented_owner),
):
    storage_id = _owned_storage_id(novel_id, owner_id)
    if chapter_num < 1 or chapter_num > 100000:
        raise HTTPException(status_code=400, detail="章节编号无效")
    content = engine.get_chapter(storage_id, chapter_num)
    if content is None:
        raise HTTPException(status_code=404, detail="章节不存在")
    draft = engine.memory.read_chapter_draft(storage_id, chapter_num)
    page = content[offset:offset + limit]
    return {
        "novel_id": novel_id,
        "chapter_num": chapter_num,
        "content": page,
        "offset": offset,
        "limit": limit,
        "total": len(content),
        "has_more": offset + len(page) < len(content),
        "draft": draft,
    }


@router.post("/novels/{novel_id}/chapters/{chapter_num}/draft")
async def save_owned_chapter_draft(
    novel_id: str,
    chapter_num: int,
    req: ChapterDraftRequest,
    owner_id: str = Depends(require_consented_owner),
):
    storage_id = _owned_storage_id(novel_id, owner_id)
    if chapter_num < 1 or chapter_num > 100_000:
        raise HTTPException(status_code=400, detail="章节编号无效")
    try:
        await asyncio.to_thread(engine.memory.write_chapter_draft, storage_id, chapter_num, req.content)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="草稿内容过长") from exc
    return {
        "saved": True,
        "novel_id": novel_id,
        "chapter_num": chapter_num,
        "length": len(req.content),
    }


@router.delete("/novels/{novel_id}")
async def delete_owned_novel(novel_id: str, owner_id: str = Depends(require_consented_owner)):
    storage_id = _owned_storage_id(novel_id, owner_id)
    if not engine.delete_novel(storage_id):
        raise HTTPException(status_code=404, detail="作品不存在")
    ownership_store.mark_deleted(storage_id, owner_id)
    return {"success": True, "novel_id": novel_id}


@router.post("/novels/{novel_id}/chapter-jobs", status_code=status.HTTP_202_ACCEPTED)
async def create_chapter_job(
    novel_id: str,
    req: ChapterJobRequest,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    owner_id: str = Depends(require_consented_owner),
):
    storage_id = _owned_storage_id(novel_id, owner_id)
    if not idempotency_key or not idempotency_key.strip():
        raise HTTPException(status_code=400, detail="缺少 Idempotency-Key")
    # v3.8: 锁文件预检——SSE 长生成与 job 共享 .generating_{chapter}.lock。
    # 此前 job 创建不做预检：若 SSE 正在生成该章，job 会消费到 error 事件后失败，
    # 用户看到的是"失败"而非"排队"。这里命中活跃锁时直接返回排队语义（409）。
    try:
        import os as _os
        from core.mixins.generation import _gen_lock_is_stale
        lock_file = _os.path.join(
            engine.memory.get_novel_dir(storage_id),
            f".generating_{int(req.chapter_num):04d}.lock",
        )
        if _os.path.exists(lock_file) and not _gen_lock_is_stale(lock_file):
            raise HTTPException(
                status_code=409,
                detail=f"第{req.chapter_num}章正在生成中，请等待完成后再试",
            )
    except HTTPException:
        raise
    except Exception:
        pass  # 预检失败不阻断任务（锁文件缺失等场景继续走正常流程）
    request_data = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    quota_request = {"novel_id": storage_id, **request_data}
    request_hash = hashlib.sha256(
        json.dumps(quota_request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    try:
        quota_reservation = quota_store.reserve(
            owner_id=owner_id,
            reservation_key=idempotency_key,
            request_hash=request_hash,
            token_budget=MAX_CHAPTER_TOKENS,
            daily_job_limit=DAILY_CHAPTER_JOB_LIMIT,
            daily_token_limit=DAILY_TOKEN_LIMIT,
        )
    except QuotaExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="幂等键已绑定其他任务") from exc
    try:
        job = job_store.create_or_get(
            job_type="chapter",
            novel_id=storage_id,
            request=request_data,
            owner_id=owner_id,
            idempotency_key=idempotency_key,
            max_active=MAX_ACTIVE_JOBS_PER_OWNER,
        )
    except JobCapacityExceeded as exc:
        if not quota_reservation.get("replayed"):
            quota_store.release(
                owner_id=owner_id,
                reservation_key=idempotency_key,
                request_hash=request_hash,
            )
        raise HTTPException(status_code=429, detail="当前已有任务在生成，请稍后再试") from exc
    except ValueError as exc:
        if not quota_reservation.get("replayed"):
            quota_store.release(
                owner_id=owner_id,
                reservation_key=idempotency_key,
                request_hash=request_hash,
            )
        raise HTTPException(status_code=409, detail="幂等键已绑定其他任务") from exc
    _start_chapter_task(job, req)
    return _public_job(job_store.get(job["job_id"]) or job)


@router.post("/novels/{novel_id}/exports", status_code=status.HTTP_202_ACCEPTED)
async def create_export_job(
    novel_id: str,
    req: ExportJobRequest,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    owner_id: str = Depends(require_consented_owner),
):
    """Queue a file export; content is never embedded in the JSON response."""

    storage_id = _owned_storage_id(novel_id, owner_id)
    if not idempotency_key or not idempotency_key.strip():
        raise HTTPException(status_code=400, detail="缺少 Idempotency-Key")
    request_data = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    try:
        job = job_store.create_or_get(
            job_type="export",
            novel_id=storage_id,
            request=request_data,
            owner_id=owner_id,
            idempotency_key=idempotency_key,
            max_active=MAX_ACTIVE_JOBS_PER_OWNER,
        )
    except JobCapacityExceeded as exc:
        raise HTTPException(status_code=429, detail="当前已有任务在处理，请稍后再试") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="幂等键已绑定其他任务") from exc
    _start_export_task(job, ExportJobRequest(**request_data))
    return _public_job(job_store.get(job["job_id"]) or job)


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, owner_id: str = Depends(require_consented_owner)):
    return _public_job(_owned_job(job_id, owner_id))


@router.get("/exports/{job_id}/download")
async def download_export(job_id: str, owner_id: str = Depends(require_consented_owner)):
    job = _owned_job(job_id, owner_id)
    if job["job_type"] != "export":
        raise HTTPException(status_code=404, detail="导出任务不存在")
    if job["status"] != JobStatus.SUCCEEDED or not isinstance(job.get("result"), dict):
        raise HTTPException(status_code=409, detail="导出尚未完成")
    result = job["result"]
    expires_at = result.get("expires_at")
    if isinstance(expires_at, (int, float)) and expires_at <= time.time():
        raise HTTPException(status_code=410, detail="导出文件已过期，请重新导出")
    path_value = result.get("path")
    if not isinstance(path_value, str):
        raise HTTPException(status_code=404, detail="导出文件不存在")
    export_root = EXPORT_DIR.resolve()
    path = Path(path_value).resolve()
    try:
        path.relative_to(export_root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="导出文件不存在") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="导出文件不存在")
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=str(result.get("filename") or path.name),
    )


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, owner_id: str = Depends(require_consented_owner)):
    job = _owned_job(job_id, owner_id)
    updated = job_store.request_cancel(job["job_id"])
    task = _active_tasks.get(job["job_id"])
    if task and not task.done():
        task.cancel()
    return _public_job(updated)
