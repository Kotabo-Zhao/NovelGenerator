"""Novels CRUD, blueprint planning, and export routes."""

import asyncio
from typing import List
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..core.exporter import export_novel_text
from ..core.models import ChapterContract

router = APIRouter(prefix="/api/novels", tags=["novels"])


class CreateNovelRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)
    genre: str = "都市悬疑"
    style: str = "克制镜头"
    mode: str = "web"
    premise: str = ""
    pov: str = "第三人称限知"
    target_chapters: int = Field(default=12, ge=1, le=500)
    target_words_per_chapter: int = Field(default=2000, ge=200, le=20000)
    auto_plan: bool = True


class UpdateContractRequest(BaseModel):
    title: str
    summary: str = ""
    goal: str = ""
    conflict: str = ""
    characters: List[str] = Field(default_factory=list)
    must_happen: List[str] = Field(default_factory=list)
    hook_direction: str = ""
    target_words: int = Field(default=2000, ge=200, le=20000)
    pov: str = "第三人称限知"


@router.get("")
async def list_novels(request: Request):
    db = request.app.state.db
    novels = await asyncio.to_thread(db.list_novels)
    return {"novels": novels}


@router.post("")
async def create_novel(req: CreateNovelRequest, request: Request):
    db = request.app.state.db
    engine = request.app.state.engine
    novel = await asyncio.to_thread(
        db.create_novel,
        title=req.title,
        genre=req.genre,
        style=req.style,
        mode=req.mode,
        premise=req.premise,
        pov=req.pov,
        target_chapters=req.target_chapters,
        target_words_per_chapter=req.target_words_per_chapter,
    )
    if req.auto_plan:
        planned = await engine.plan_novel(novel["id"], chapter_count=min(req.target_chapters, 6))
        return planned
    return {"novel": novel, "ledger": await asyncio.to_thread(db.get_ledger, novel["id"]), "planned_count": 0}


@router.get("/{novel_id}")
async def get_novel(novel_id: str, request: Request):
    db = request.app.state.db
    novel = await asyncio.to_thread(db.get_novel, novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="作品不存在")
    return novel


@router.delete("/{novel_id}")
async def delete_novel(novel_id: str, request: Request):
    db = request.app.state.db
    deleted = await asyncio.to_thread(db.delete_novel, novel_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="作品不存在")
    return {"status": "deleted", "novel_id": novel_id}


@router.post("/{novel_id}/plan")
async def plan_novel(novel_id: str, request: Request, chapter_count: int = 6):
    engine = request.app.state.engine
    try:
        return await engine.plan_novel(novel_id, chapter_count=chapter_count)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.put("/{novel_id}/chapters/{chapter_num}/contract")
async def update_chapter_contract(novel_id: str, chapter_num: int, req: UpdateContractRequest, request: Request):
    db = request.app.state.db
    novel = await asyncio.to_thread(db.get_novel, novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="作品不存在")
    payload_dict = req.model_dump() if hasattr(req, 'model_dump') else req.dict()
    contract = ChapterContract(chapter_num=chapter_num, **payload_dict)
    await asyncio.to_thread(db.save_chapter_contracts, novel_id, [contract])
    return {"status": "ok", "contract": contract.to_dict()}


@router.get("/{novel_id}/export")
async def export_novel(novel_id: str, request: Request, fmt: str = "txt"):
    db = request.app.state.db
    try:
        content, media_type, filename = await asyncio.to_thread(export_novel_text, db, novel_id, fmt)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    encoded = quote(filename)
    return Response(
        content=content.encode("utf-8"),
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded}"},
    )
