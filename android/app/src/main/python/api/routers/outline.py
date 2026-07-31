"""NovelGenerator — 大纲交互 / 反馈拆解 / 章节反馈 API Router"""
import json
import os
from typing import Optional
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

router = APIRouter()

from ..deps import engine, log, _validate_novel_id, _validate_chapter_range, _sse_with_heartbeat

@router.post("/api/novels/{novel_id}/regenerate-outline")
async def regenerate_outline(novel_id: str, req: dict):
    """根据修改意见重新生成大纲（保留世界观和角色）+ 心跳"""
    feedback = req.get("feedback", "")
    if not feedback.strip():
        raise HTTPException(status_code=400, detail="请输入修改意见")

    async def event_stream():
        async for data in _sse_with_heartbeat(
            engine.regenerate_outline_stream(novel_id, feedback)
        ):
            yield data

    return StreamingResponse(event_stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/api/novels/{novel_id}/interactive-outline")
async def interactive_outline(novel_id: str, req: dict):
    """v2 交互式大纲: FeedbackDecomposer 语义拆解 → 逐条精确执行 → diff输出 + 心跳"""
    feedback = req.get("feedback", "")
    if not feedback.strip():
        raise HTTPException(status_code=400, detail="请输入修改意见")

    async def event_stream():
        async for data in _sse_with_heartbeat(
            engine.interactive_outline_stream(novel_id, feedback)
        ):
            yield data

    return StreamingResponse(event_stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/api/novels/{novel_id}/decompose-feedback")
async def decompose_feedback(novel_id: str, req: dict):
    """仅拆解反馈为修改计划（不执行），供前端预览"""
    feedback = req.get("feedback", "")
    if not feedback.strip():
        raise HTTPException(status_code=400, detail="请输入修改意见")
    result = engine.decompose_feedback(novel_id, feedback)
    return {"result": result}


@router.post("/api/novels/{novel_id}/chapter-feedback/{chapter_num}")
async def chapter_feedback(novel_id: str, chapter_num: int, req: dict):
    """对已生成章节提出修改意见 — 拆解为具体指令"""
    feedback = req.get("feedback", "")
    if not feedback.strip():
        raise HTTPException(status_code=400, detail="请输入修改意见")

    plan = engine.get_novel(novel_id)
    if not plan:
        raise HTTPException(status_code=404, detail=f"小说 '{novel_id}' 不存在")

    chapter_outline = engine._find_chapter_outline(plan, chapter_num)
    if not chapter_outline:
        raise HTTPException(status_code=404, detail=f"第{chapter_num}章大纲不存在")

    result = engine.feedback_decomposer.decompose_for_chapter(
        feedback, chapter_num, chapter_outline, plan
    )
    return {"result": result}

