"""System status, styles catalog, and LLM settings routes (never returns plaintext API keys)."""

import asyncio
from typing import Any, Dict

from fastapi import APIRouter, Request

from ..core.writing_engine import STYLE_ATTRIBUTES

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/health")
async def health(request: Request):
    cfg = request.app.state.config
    settings = await asyncio.to_thread(request.app.state.llm_store.load)
    return {
        "status": "ok",
        "version": "5.0.0",
        "storage": "sqlite-wal-fts5",
        "mode": settings.mode,
        "model": settings.model,
        "data_dir": str(cfg.data_dir),
    }


@router.get("/styles")
async def list_styles():
    return {
        "styles": [{"name": k, "description": v} for k, v in STYLE_ATTRIBUTES.items()],
        "genres": ["都市悬疑", "古风权谋", "硬核科幻", "现代奇幻", "现实文学", "历史架空", "轻喜脑洞"],
    }


@router.get("/llm")
async def get_llm_settings(request: Request):
    settings = await asyncio.to_thread(request.app.state.llm_store.load)
    return settings.to_public_dict()


@router.put("/llm")
async def update_llm_settings(payload: Dict[str, Any], request: Request):
    settings = await asyncio.to_thread(request.app.state.llm_store.save, payload)
    return settings.to_public_dict()


@router.post("/llm/test")
async def test_llm_connection(request: Request):
    return await request.app.state.llm.test_connection()
