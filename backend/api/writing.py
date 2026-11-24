"""Streaming chapter drafting, surgical rewrite, craft check, and version history routes."""

import asyncio
import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..core.craft_analyzer import analyze_prose_craft

router = APIRouter(prefix="/api/novels/{novel_id}/chapters", tags=["writing"])


class DraftRequest(BaseModel):
    revision_feedback: str = ""
    auto_commit: bool = True


class SaveChapterRequest(BaseModel):
    content: str = Field(..., min_length=1)
    note: str = "手动保存"
    extract_state: bool = False


class SurgicalRewriteRequest(BaseModel):
    selected_text: str = Field(..., min_length=4)
    instruction: str = Field(..., min_length=2)


class CraftCheckRequest(BaseModel):
    text: Optional[str] = None


@router.get("/{chapter_num}")
async def get_chapter(novel_id: str, chapter_num: int, request: Request):
    db = request.app.state.db
    chapter = await asyncio.to_thread(db.get_chapter, novel_id, chapter_num)
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    versions = await asyncio.to_thread(db.list_chapter_versions, novel_id, chapter_num)
    craft = await asyncio.to_thread(analyze_prose_craft, chapter["content"]) if chapter["content"] else None
    return {**chapter, "versions": versions, "craft": craft}


@router.put("/{chapter_num}")
async def save_chapter(novel_id: str, chapter_num: int, req: SaveChapterRequest, request: Request):
    db = request.app.state.db
    engine = request.app.state.engine
    craft = await asyncio.to_thread(analyze_prose_craft, req.content)
    delta = None
    if req.extract_state:
        delta = await engine.extract_state_delta(chapter_num, req.content)
    chapter = await asyncio.to_thread(
        db.commit_chapter_and_state,
        novel_id,
        chapter_num,
        content=req.content,
        craft_score=craft["craft_score"],
        ai_score=craft["ai_score"],
        version_note=req.note,
        delta=delta,
    )
    versions = await asyncio.to_thread(db.list_chapter_versions, novel_id, chapter_num)
    return {**chapter, "versions": versions, "craft": craft}


@router.post("/{chapter_num}/draft-stream")
async def draft_chapter_stream(novel_id: str, chapter_num: int, req: DraftRequest, request: Request):
    engine = request.app.state.engine

    async def event_stream():
        try:
            async for evt in engine.draft_chapter_stream(
                novel_id,
                chapter_num,
                revision_feedback=req.revision_feedback,
                auto_commit=req.auto_commit,
            ):
                if await request.is_disconnected():
                    break
                yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/{chapter_num}/surgical-stream")
async def surgical_rewrite_stream(novel_id: str, chapter_num: int, req: SurgicalRewriteRequest, request: Request):
    engine = request.app.state.engine

    async def event_stream():
        try:
            async for evt in engine.surgical_rewrite_stream(
                novel_id,
                chapter_num,
                selected_text=req.selected_text,
                instruction=req.instruction,
            ):
                if await request.is_disconnected():
                    break
                yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/{chapter_num}/craft-check")
async def craft_check(novel_id: str, chapter_num: int, request: Request, req: Optional[CraftCheckRequest] = None):
    db = request.app.state.db
    text = req.text if (req and req.text) else None
    if not text:
        ch = await asyncio.to_thread(db.get_chapter, novel_id, chapter_num)
        text = ch["content"] if ch else ""
    if not text:
        raise HTTPException(status_code=404, detail="章节正文为空，无法体检")
    return await asyncio.to_thread(analyze_prose_craft, text)


@router.post("/{chapter_num}/versions/{version_id}/restore")
async def restore_version(novel_id: str, chapter_num: int, version_id: int, request: Request):
    db = request.app.state.db
    restored = await asyncio.to_thread(db.restore_chapter_version, novel_id, chapter_num, version_id)
    if not restored:
        raise HTTPException(status_code=404, detail="历史版本不存在")
    versions = await asyncio.to_thread(db.list_chapter_versions, novel_id, chapter_num)
    return {**restored, "versions": versions}
