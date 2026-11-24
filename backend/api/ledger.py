"""Narrative State Ledger CRUD & FTS5 search routes."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/novels/{novel_id}/ledger", tags=["ledger"])


@router.get("")
async def get_ledger(novel_id: str, request: Request):
    db = request.app.state.db
    novel = await asyncio.to_thread(db.get_novel, novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="作品不存在")
    return await asyncio.to_thread(db.get_ledger, novel_id)


@router.post("/entities")
async def upsert_entity(novel_id: str, payload: dict[str, Any], request: Request):
    db = request.app.state.db
    try:
        return await asyncio.to_thread(db.upsert_entity, novel_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/foreshadows")
async def upsert_foreshadow(novel_id: str, payload: dict[str, Any], request: Request):
    db = request.app.state.db
    try:
        return await asyncio.to_thread(db.upsert_foreshadow, novel_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/world")
async def upsert_world_entry(novel_id: str, payload: dict[str, Any], request: Request):
    db = request.app.state.db
    try:
        return await asyncio.to_thread(db.upsert_world_entry, novel_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/search")
async def search_ledger(novel_id: str, q: str, request: Request, limit: int = 12):
    db = request.app.state.db
    hits = await asyncio.to_thread(db.search_ledger, novel_id, q, limit)
    return {"query": q, "hits": hits}
