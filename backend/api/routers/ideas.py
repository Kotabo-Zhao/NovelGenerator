"""NovelGenerator — 灵感收集箱 + 素材库 API（创作者工具 M4）

数据存 NOVELS_DIR/ideas.json（全局共享，跨小说）。
"""
import json
import os
import time
import uuid
from fastapi import APIRouter, Depends, HTTPException, Request

router = APIRouter()

from ..deps import log
from ..auth import is_internal_version, require_owner
from config import NOVELS_DIR
from core.atomic_io import atomic_write_json


def _ideas_path() -> str:
    return os.path.join(NOVELS_DIR, "ideas.json")


def _load_ideas() -> dict:
    if os.path.exists(_ideas_path()):
        try:
            with open(_ideas_path(), "r", encoding="utf-8") as f:
                d = json.load(f)
            d.setdefault("ideas", [])
            d.setdefault("materials", [])
            return d
        except Exception:
            pass
    return {"ideas": [], "materials": []}


def _save_ideas(d: dict) -> None:
    atomic_write_json(_ideas_path(), d)


def _check_owner(request: Request) -> None:
    owner_id = getattr(request.state, "owner_id", None)
    if not owner_id and not is_internal_version():
        # 该端点不在 /api/novels* 前缀下，legacy 中间件不会注入 owner_id；
        # 但路由已用 require_owner Depends 兜底鉴权，这里仅作防御。
        raise HTTPException(status_code=401, detail="需要登录")


@router.get("/api/ideas")
async def list_ideas(request: Request, tag: str = "", _owner: str = Depends(require_owner)):
    """灵感列表（可按 tag 过滤）。"""
    _check_owner(request)
    d = _load_ideas()
    ideas = d.get("ideas", [])
    if tag:
        ideas = [i for i in ideas if tag in (i.get("tags", []) or [])]
    ideas.sort(key=lambda i: i.get("created_at", ""), reverse=True)
    return {"ideas": ideas, "tags": sorted({t for i in d.get("ideas", []) for t in (i.get("tags", []) or [])})}


@router.post("/api/ideas")
async def add_idea(req: dict, request: Request, _owner: str = Depends(require_owner)):
    """新增灵感 {text, tags?}。"""
    _check_owner(request)
    text = str((req or {}).get("text", "")).strip()
    if not text:
        raise HTTPException(status_code=400, detail="灵感内容不能为空")
    tags = [str(t).strip() for t in ((req or {}).get("tags", []) or []) if str(t).strip()]
    d = _load_ideas()
    idea = {
        "id": "i_" + uuid.uuid4().hex[:8],
        "text": text[:2000],
        "tags": tags,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": (req or {}).get("source", "manual"),
    }
    d.setdefault("ideas", []).append(idea)
    _save_ideas(d)
    return {"status": "ok", "idea": idea}


@router.delete("/api/ideas/{idea_id}")
async def delete_idea(idea_id: str, request: Request, _owner: str = Depends(require_owner)):
    _check_owner(request)
    d = _load_ideas()
    d["ideas"] = [i for i in d.get("ideas", []) if i.get("id") != idea_id]
    _save_ideas(d)
    return {"status": "ok"}


@router.get("/api/ideas/materials")
async def list_materials(request: Request, cat: str = "", _owner: str = Depends(require_owner)):
    _check_owner(request)
    d = _load_ideas()
    mats = d.get("materials", [])
    if cat:
        mats = [m for m in mats if m.get("category") == cat]
    return {"materials": mats}


@router.post("/api/ideas/materials")
async def add_material(req: dict, request: Request, _owner: str = Depends(require_owner)):
    """新增素材 {category, title, content, tags?}。"""
    _check_owner(request)
    category = str((req or {}).get("category", "桥段")).strip() or "桥段"
    title = str((req or {}).get("title", "")).strip()
    content = str((req or {}).get("content", "")).strip()
    if not title or not content:
        raise HTTPException(status_code=400, detail="title 和 content 必填")
    d = _load_ideas()
    mat = {
        "id": "m_" + uuid.uuid4().hex[:8],
        "category": category,
        "title": title[:200],
        "content": content[:5000],
        "tags": [str(t).strip() for t in ((req or {}).get("tags", []) or []) if str(t).strip()],
    }
    d.setdefault("materials", []).append(mat)
    _save_ideas(d)
    return {"status": "ok", "material": mat}
