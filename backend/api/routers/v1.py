"""Authenticated, task-oriented API for the Mini Program migration path."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from typing import Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from core.job_runner import ChapterJobRunner
from core.path_security import validate_novel_id

from ..auth import require_owner
from ..deps import engine, job_store, log, ownership_store
from .novels import CreateNovelRequest


router = APIRouter(prefix="/v1", tags=["authenticated-v1"])
_active_tasks: Dict[str, asyncio.Task] = {}


class ChapterJobRequest(BaseModel):
    chapter_num: int = Field(..., ge=1, le=100000)
    writing_mode: str = Field("webnovel", pattern="^(webnovel|literary)$")
    feedback: Optional[str] = Field(None, max_length=5000)


def _public_job(job: dict) -> dict:
    """Return only client-safe task fields; owner_id never crosses this boundary."""

    return {
        "job_id": job["job_id"],
        "novel_id": job["novel_id"],
        "job_type": job["job_type"],
        "status": job["status"],
        "progress": job["progress"],
        "phase": job["phase"],
        "result": job["result"],
        "error_code": job["error_code"],
        "error_message": job["error_message"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
        "started_at": job["started_at"],
        "finished_at": job["finished_at"],
    }


def _owned_novel(novel_id: str, owner_id: str) -> dict:
    try:
        normalized_id = validate_novel_id(novel_id)
        ownership_store.require_owner(normalized_id, owner_id)
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=404, detail="作品不存在") from exc
    plan = engine.get_novel(normalized_id)
    if not plan:
        raise HTTPException(status_code=404, detail="作品不存在")
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

    runner = ChapterJobRunner(engine, job_store)
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


@router.get("/novels")
async def list_owned_novels(owner_id: str = Depends(require_owner)):
    result = []
    for record in ownership_store.list_for_owner(owner_id):
        plan = engine.get_novel(record["novel_id"])
        if not plan:
            continue
        state = engine.memory.get_novel_state(record["novel_id"])
        result.append(
            {
                "id": record["novel_id"],
                "title": plan.get("title", record["novel_id"]),
                "genre": plan.get("genre", ""),
                "style": plan.get("style", ""),
                "state": state,
            }
        )
    return {"novels": result}


@router.post("/novels", status_code=status.HTTP_201_CREATED)
async def create_owned_novel(
    req: CreateNovelRequest,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    owner_id: str = Depends(require_owner),
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
            return {
                "novel": replayed_plan,
                "novel_id": reservation["novel_id"],
                "replayed": True,
            }
        raise HTTPException(status_code=409, detail="创建任务结果已失效，请重新提交")
    if reservation.get("replayed"):
        raise HTTPException(status_code=409, detail="相同创建请求正在处理中")
    try:
        plan = await asyncio.to_thread(engine.create_novel, creative_input)
        novel_id = validate_novel_id(plan.get("title", ""))
        ownership_store.claim(novel_id, owner_id)
        ownership_store.complete_creation(owner_id, idempotency_key, request_hash, novel_id)
    except (ValueError, PermissionError) as exc:
        ownership_store.fail_creation(owner_id, idempotency_key, request_hash, "作品创建结果无法绑定当前用户")
        raise HTTPException(status_code=409, detail="作品创建结果无法绑定当前用户") from exc
    except Exception as exc:
        ownership_store.fail_creation(owner_id, idempotency_key, request_hash, "作品创建失败")
        log.exception("Authenticated novel creation failed")
        raise HTTPException(status_code=500, detail="作品创建失败，请稍后重试") from exc
    return {"novel": plan, "novel_id": novel_id, "replayed": False}


@router.get("/novels/{novel_id}")
async def get_owned_novel(novel_id: str, owner_id: str = Depends(require_owner)):
    plan = _owned_novel(novel_id, owner_id)
    plan.pop("chapters", None)
    return {"novel": plan}


@router.get("/novels/{novel_id}/chapters/{chapter_num}")
async def get_owned_chapter(
    novel_id: str,
    chapter_num: int,
    owner_id: str = Depends(require_owner),
):
    _owned_novel(novel_id, owner_id)
    if chapter_num < 1 or chapter_num > 100000:
        raise HTTPException(status_code=400, detail="章节编号无效")
    content = engine.get_chapter(novel_id, chapter_num)
    if content is None:
        raise HTTPException(status_code=404, detail="章节不存在")
    return {"novel_id": novel_id, "chapter_num": chapter_num, "content": content}


@router.delete("/novels/{novel_id}")
async def delete_owned_novel(novel_id: str, owner_id: str = Depends(require_owner)):
    _owned_novel(novel_id, owner_id)
    if not engine.delete_novel(novel_id):
        raise HTTPException(status_code=404, detail="作品不存在")
    ownership_store.mark_deleted(novel_id, owner_id)
    return {"success": True, "novel_id": novel_id}


@router.post("/novels/{novel_id}/chapter-jobs", status_code=status.HTTP_202_ACCEPTED)
async def create_chapter_job(
    novel_id: str,
    req: ChapterJobRequest,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    owner_id: str = Depends(require_owner),
):
    _owned_novel(novel_id, owner_id)
    if not idempotency_key or not idempotency_key.strip():
        raise HTTPException(status_code=400, detail="缺少 Idempotency-Key")
    request_data = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    try:
        job = job_store.create_or_get(
            job_type="chapter",
            novel_id=novel_id,
            request=request_data,
            owner_id=owner_id,
            idempotency_key=idempotency_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="幂等键已绑定其他任务") from exc
    _start_chapter_task(job, req)
    return _public_job(job_store.get(job["job_id"]) or job)


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, owner_id: str = Depends(require_owner)):
    return _public_job(_owned_job(job_id, owner_id))


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, owner_id: str = Depends(require_owner)):
    job = _owned_job(job_id, owner_id)
    updated = job_store.request_cancel(job["job_id"])
    task = _active_tasks.get(job["job_id"])
    if task and not task.done():
        task.cancel()
    return _public_job(updated)
