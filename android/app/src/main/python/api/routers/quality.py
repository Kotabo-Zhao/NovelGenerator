"""NovelGenerator — 逻辑校验 / 一致性 / 开局 / 反转 / 节奏 / 摘要 API Router"""
import json
import os
from typing import Optional
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

router = APIRouter()

from ..deps import engine, log, _validate_novel_id, _validate_chapter_range
from core.chapter_summarizer import check_and_compress

@router.post("/api/novels/{novel_id}/logic-check/{chapter_num}")
async def logic_check_chapter(novel_id: str, chapter_num: int, req: dict = None):
    """全维度逻辑监督 — 12 大类逻辑错误检测 + 分类得分 + 修复提示"""
    run_deep = req.get("run_deep", True) if req else True
    result = engine.validate_chapter_consistency(novel_id, chapter_num, run_deep=run_deep)
    # 附加修复提示
    if result.get("violations") or result.get("warnings"):
        result["fix_prompt"] = engine.build_logic_fix_prompt(result)
    return {"result": result}


@router.post("/api/novels/{novel_id}/logic-check-batch")
async def logic_check_batch(novel_id: str, req: dict):
    """批量逻辑监督（L1快速扫描，无LLM调用）"""
    plan = engine.get_novel(novel_id)
    if not plan:
        raise HTTPException(404, "小说不存在")

    start = req.get("start", 1)
    end = req.get("end", 1)
    chapters = {}
    for ch in range(start, end + 1):
        content = engine.get_chapter(novel_id, ch)
        if content:
            chapters[ch] = content

    result = engine.logic_supervisor.validate_chapter_batch(chapters, plan)
    return {"result": result}


@router.post("/api/novels/{novel_id}/validate-chapter/{chapter_num}")
async def validate_chapter_consistency(novel_id: str, chapter_num: int, req: dict = None):
    """对已生成章节执行逻辑一致性校验"""
    run_deep = req.get("run_deep", True) if req else True
    result = engine.validate_chapter_consistency(novel_id, chapter_num, run_deep=run_deep)
    return {"result": result}


@router.get("/api/novels/{novel_id}/validate-outline")
async def validate_outline_consistency(novel_id: str):
    """校验大纲逻辑一致性"""
    result = engine.validate_outline_consistency(novel_id)
    return {"result": result}


@router.post("/api/novels/{novel_id}/analyze-opening")
async def analyze_opening(novel_id: str, req: dict = None):
    """分析章节开头吸引力"""
    chapter_num = req.get("chapter_num", 1) if req else 1
    result = engine.analyze_opening(novel_id, chapter_num)
    return {"result": result}


@router.post("/api/novels/{novel_id}/opening-alternatives")
async def opening_alternatives(novel_id: str, req: dict):
    """生成替代开头方案"""
    chapter_num = req.get("chapter_num", 1)
    count = req.get("count", 3)
    result = await engine.generate_opening_alternatives(novel_id, chapter_num, count)
    return {"alternatives": result}


@router.get("/api/novels/{novel_id}/design-twists")
async def design_twists(novel_id: str):
    """为整部小说规划反转点"""
    result = engine.design_twists(novel_id)
    return {"result": result}


@router.post("/api/novels/{novel_id}/summarize")
async def summarize_chapters(novel_id: str, req: dict):
    """触发渐进式摘要压缩"""
    chapter_num = req.get("chapter_num", 0)
    result = check_and_compress(engine.memory, novel_id, chapter_num, engine.chapter_summarizer)
    return {"result": result}


@router.get("/api/novels/{novel_id}/token-budget")
async def get_token_budget(novel_id: str):
    """查看当前小说的 token 预算"""
    state = engine.memory.get_novel_state(novel_id)
    total = state.get("total_chapters", 0)
    current = state.get("current_chapter", 0)
    budget = engine.chapter_summarizer.get_token_budget(current)
    return {"total_chapters": total, "current_chapter": current, "budget": budget}


@router.post("/api/novels/{novel_id}/design-chapter-twist")
async def design_chapter_twist(novel_id: str, req: dict):
    """为单章设计反转钩子"""
    chapter_num = req.get("chapter_num", 1)
    result = engine.design_chapter_twist(novel_id, chapter_num)
    return {"result": result}


@router.post("/api/novels/{novel_id}/pacing-check/{chapter_num}")
async def check_pacing(novel_id: str, chapter_num: int):
    """检查单章节奏质量"""
    content = engine.get_chapter(novel_id, chapter_num)
    if content is None:
        raise HTTPException(status_code=404, detail=f"第{chapter_num}章不存在")

    result = engine.pacing_checker.analyze(content, chapter_num)
    return {"result": result}

