"""NovelGenerator — 需求拆解与监督 API Router"""
import json
import os
from typing import Optional
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

router = APIRouter()

from ..deps import engine, log, _validate_novel_id, _validate_chapter_range, _sse_with_heartbeat

@router.post("/api/requirements/preview-decompose")
async def preview_decompose(req: dict):
    """预拆解用户灵感（创建小说前预览需求拆解结果）

    v2.2: 新增端点，让用户在创建小说之前预览AI对其需求的理解。
    不依赖 novel_id，直接拆解灵感文本。
    """
    inspiration = req.get("inspiration", "")
    if not inspiration.strip():
        raise HTTPException(status_code=400, detail="请输入灵感或需求")

    # 直接拆解，不关联任何小说
    result = engine.requirement_decomposer.decompose(inspiration)

    # 同时返回阶段上下文
    phase_context = engine.requirement_decomposer.decompose_to_context(result)

    return {
        "requirements": result,
        "phase_context": {
            "worldbuilding_count": len(phase_context.get("worldbuilding_context", "").split("---")) if phase_context.get("worldbuilding_context") else 0,
            "character_count": len(phase_context.get("character_context", "").split("---")) if phase_context.get("character_context") else 0,
            "outline_count": len(phase_context.get("outline_context", "").split("---")) if phase_context.get("outline_context") else 0,
            "p0_count": len(phase_context.get("p0_requirements", [])),
        }
    }


@router.post("/api/novels/{novel_id}/requirements/decompose")
async def decompose_requirements(novel_id: str, req: dict):
    """拆解用户灵感为可执行子任务（关联已创建的小说）"""
    inspiration = req.get("inspiration", "")
    if not inspiration.strip():
        raise HTTPException(status_code=400, detail="请输入灵感或需求")
    result = engine.decompose_requirements(novel_id, inspiration)
    return {"requirements": result}


@router.post("/api/novels/{novel_id}/requirements/update")
async def update_requirements(novel_id: str, req: dict):
    """追加/修改需求"""
    inspiration = req.get("inspiration", "")
    if not inspiration.strip():
        raise HTTPException(status_code=400, detail="请输入追加的需求")
    result = engine.update_requirements(novel_id, inspiration)
    return {"requirements": result}


@router.get("/api/novels/{novel_id}/requirements")
async def get_requirements(novel_id: str):
    """获取当前需求列表"""
    reqs = engine._requirements.get(novel_id, {})
    if not reqs:
        raise HTTPException(status_code=404, detail="尚未拆解需求")
    return reqs


@router.post("/api/novels/{novel_id}/requirements/supervise")
async def supervise_requirements(novel_id: str):
    """监督当前方案是否满足需求"""
    result = engine.supervise_requirements(novel_id)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/api/novels/{novel_id}/requirements/verify-loop")
async def verify_and_fix_loop(novel_id: str):
    """循环校验：监督→修正→再监督"""
    async def event_stream():
        async for data in _sse_with_heartbeat(
            engine.verify_and_fix_loop(novel_id)
        ):
            yield data

    return StreamingResponse(event_stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

