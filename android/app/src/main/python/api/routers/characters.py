"""NovelGenerator — 角色人设 API Router（女娲框架蒸馏）

POST /api/novels/{novel_id}/characters/{char_name}/profile  蒸馏人设（LLM 1-2 分钟）
GET  /api/novels/{novel_id}/character-profiles              列出全部人设卡
GET  /api/novels/{novel_id}/characters/{char_name}/profile  获取单个人设卡
"""
import json
import os
from typing import Optional
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

router = APIRouter()

from ..deps import engine, log, _validate_novel_id, _validate_chapter_range


@router.post("/api/novels/{novel_id}/characters/{char_name}/profile")
async def distill_character_profile(novel_id: str, char_name: str):
    """蒸馏角色人设卡（女娲框架：心智模型/决策启发式/表达DNA/反模式/边界）"""
    _validate_novel_id(novel_id)
    if not char_name or len(char_name) > 50:
        raise HTTPException(status_code=400, detail="非法角色名")

    result = engine.distill_character_profile(novel_id, char_name)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return {"ok": True, "profile": result}


@router.get("/api/novels/{novel_id}/character-profiles")
async def list_character_profiles(novel_id: str):
    """列出全部已蒸馏角色人设卡"""
    _validate_novel_id(novel_id)
    profiles = engine.get_character_profiles(novel_id)
    # 返回精简摘要列表
    summary = {}
    for name, prof in profiles.items():
        summary[name] = {
            "mental_models": [m.get("name", "") if isinstance(m, dict) else str(m)[:40]
                              for m in prof.get("mental_models", [])],
            "heuristic_count": len(prof.get("decision_heuristics", [])),
            "dna_count": len(prof.get("expression_dna", [])),
            "distilled_at": prof.get("distilled_at", ""),
        }
    return {"ok": True, "profiles": summary}


@router.get("/api/novels/{novel_id}/characters/{char_name}/profile")
async def get_character_profile(novel_id: str, char_name: str):
    """获取单个人设卡"""
    _validate_novel_id(novel_id)
    profile = engine.get_character_profile(novel_id, char_name)
    if not profile:
        raise HTTPException(status_code=404, detail=f"「{char_name}」尚未蒸馏人设，请先 POST 蒸馏")
    return {"ok": True, "profile": profile}


@router.post("/api/novels/{novel_id}/character-profiles/generate-all")
async def generate_all_assets(novel_id: str):
    """一键补全：为已有书批量蒸馏全部角色人设 + 生成声音卡，并回写人物宝典

    旧书迁移用（创建即蒸馏上线前的书没有这些资产）。
    阻塞式，约 1-3 分钟。
    """
    _validate_novel_id(novel_id)
    import asyncio
    result = await asyncio.to_thread(engine.generate_all_character_assets, novel_id)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return {"ok": True, "result": result}

