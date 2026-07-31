"""NovelGenerator — 章节反馈 API Router（反馈闭环）

POST /api/novels/{novel_id}/chapters/{chapter_num}/feedback  提交 👍👎 + 理由
GET  /api/novels/{novel_id}/feedback                          反馈汇总
"""
import json
import os
from typing import Optional
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

router = APIRouter()

from ..deps import engine, log, _validate_novel_id, _validate_chapter_range


@router.post("/api/novels/{novel_id}/chapters/{chapter_num}/feedback")
async def submit_feedback(novel_id: str, chapter_num: int, req: dict):
    """提交章节反馈（rating: 1 赞 / -1 踩 / 0 中性，reason 可选）"""
    _validate_novel_id(novel_id)
    _validate_chapter_range(chapter_num, chapter_num)
    rating = int(req.get("rating", 0)) if req else 0
    reason = (req.get("reason") or "").strip()[:200] if req else ""
    if rating not in (1, -1, 0):
        raise HTTPException(status_code=400, detail="rating 必须为 1 / -1 / 0")
    result = engine.submit_chapter_feedback(novel_id, chapter_num, rating, reason)
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    return result


@router.get("/api/novels/{novel_id}/feedback")
async def get_feedback(novel_id: str):
    """获取反馈汇总（列表 + 统计）"""
    _validate_novel_id(novel_id)
    return engine.get_feedback_summary(novel_id)
