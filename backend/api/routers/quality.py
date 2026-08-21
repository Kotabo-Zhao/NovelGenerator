"""NovelGenerator — 逻辑校验 / 一致性 / 开局 / 反转 / 节奏 / 摘要 API Router"""
import json
import os
from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

router = APIRouter()

from ..deps import engine, log, _validate_novel_id, _validate_chapter_range
from core.chapter_summarizer import check_and_compress
from .novels import _require_legacy_owner

@router.post("/api/novels/{novel_id}/logic-check/{chapter_num}")
async def logic_check_chapter(novel_id: str, chapter_num: int, req: dict = None, request: Request = None):
    """全维度逻辑监督 — 12 大类逻辑错误检测 + 分类得分 + 修复提示"""
    _require_legacy_owner(request, novel_id)
    run_deep = req.get("run_deep", True) if req else True
    # v3.8: L2 深度校验含同步 LLM（可达分钟级），放线程池避免阻塞事件循环
    import asyncio
    result = await asyncio.to_thread(
        engine.validate_chapter_consistency, novel_id, chapter_num, run_deep=run_deep
    )
    # 附加修复提示
    if result.get("violations") or result.get("warnings"):
        result["fix_prompt"] = engine.build_logic_fix_prompt(result)
    return {"result": result}


@router.post("/api/novels/{novel_id}/logic-check-batch")
async def logic_check_batch(novel_id: str, req: dict, request: Request):
    """批量逻辑监督（L1快速扫描，无LLM调用）"""
    _require_legacy_owner(request, novel_id)
    plan = engine.get_novel(novel_id)
    if not plan:
        raise HTTPException(404, "小说不存在")

    start = req.get("start", 1)
    end = req.get("end", 1)
    # v3.8: 边界防御——批量校验 N 章 O(N) 扫描，限制单次范围防成本放大
    start = max(1, int(start))
    end = max(start, int(end))
    if end - start + 1 > 200:
        raise HTTPException(400, "单次批量校验最多 200 章")
    chapters = {}
    for ch in range(start, end + 1):
        content = engine.get_chapter(novel_id, ch)
        if content:
            chapters[ch] = content

    # v3.8: O(N) 同步扫描放线程池，避免阻塞事件循环
    import asyncio
    result = await asyncio.to_thread(engine.logic_supervisor.validate_chapter_batch, chapters, plan)
    return {"result": result}


@router.post("/api/novels/{novel_id}/validate-chapter/{chapter_num}")
async def validate_chapter_consistency(novel_id: str, chapter_num: int, req: dict = None, request: Request = None):
    """对已生成章节执行逻辑一致性校验"""
    _require_legacy_owner(request, novel_id)
    run_deep = req.get("run_deep", True) if req else True
    # v3.8: 同步 LLM 校验放线程池，避免阻塞事件循环
    import asyncio
    result = await asyncio.to_thread(
        engine.validate_chapter_consistency, novel_id, chapter_num, run_deep=run_deep
    )
    return {"result": result}


@router.get("/api/novels/{novel_id}/validate-outline")
async def validate_outline_consistency(novel_id: str, request: Request):
    """校验大纲逻辑一致性"""
    _require_legacy_owner(request, novel_id)
    # v3.8: 同步 LLM 校验放线程池，避免阻塞事件循环
    import asyncio
    result = await asyncio.to_thread(engine.validate_outline_consistency, novel_id)
    return {"result": result}


@router.post("/api/novels/{novel_id}/analyze-opening")
async def analyze_opening(novel_id: str, req: dict = None, request: Request = None):
    """分析章节开头吸引力"""
    _require_legacy_owner(request, novel_id)
    chapter_num = req.get("chapter_num", 1) if req else 1
    # v3.8: 同步分析放线程池，避免阻塞事件循环
    import asyncio
    result = await asyncio.to_thread(engine.analyze_opening, novel_id, chapter_num)
    return {"result": result}


@router.post("/api/novels/{novel_id}/opening-alternatives")
async def opening_alternatives(novel_id: str, req: dict, request: Request):
    """生成替代开头方案"""
    _require_legacy_owner(request, novel_id)
    chapter_num = req.get("chapter_num", 1)
    count = req.get("count", 3)
    # v2.8.5: 内含同步 LLM，放线程池避免阻塞事件循环（同文件其他端点已遵循此模式）
    import asyncio
    result = await asyncio.to_thread(engine.generate_opening_alternatives, novel_id, chapter_num, count)
    return {"alternatives": result}


@router.get("/api/novels/{novel_id}/design-twists")
async def design_twists(novel_id: str, request: Request):
    """为整部小说规划反转点"""
    _require_legacy_owner(request, novel_id)
    # v3.8: 同步 LLM 规划放线程池，避免阻塞事件循环
    import asyncio
    result = await asyncio.to_thread(engine.design_twists, novel_id)
    return {"result": result}


@router.post("/api/novels/{novel_id}/summarize")
async def summarize_chapters(novel_id: str, req: dict, request: Request):
    """触发渐进式摘要压缩"""
    _require_legacy_owner(request, novel_id)
    chapter_num = req.get("chapter_num", 0)
    # v3.8: 摘要压缩含同步 LLM，放线程池避免阻塞事件循环
    import asyncio
    result = await asyncio.to_thread(
        check_and_compress, engine.memory, novel_id, chapter_num, engine.chapter_summarizer
    )
    return {"result": result}


@router.get("/api/novels/{novel_id}/token-budget")
async def get_token_budget(novel_id: str, request: Request):
    """查看当前小说的 token 预算"""
    _require_legacy_owner(request, novel_id)
    state = engine.memory.get_novel_state(novel_id)
    total = state.get("total_chapters", 0)
    current = state.get("current_chapter", 0)
    budget = engine.chapter_summarizer.get_token_budget(current)
    return {"total_chapters": total, "current_chapter": current, "budget": budget}


@router.post("/api/novels/{novel_id}/design-chapter-twist")
async def design_chapter_twist(novel_id: str, req: dict, request: Request):
    """为单章设计反转钩子"""
    _require_legacy_owner(request, novel_id)
    chapter_num = req.get("chapter_num", 1)
    # v3.8: 同步 LLM 调用放线程池，避免阻塞事件循环
    import asyncio
    result = await asyncio.to_thread(engine.design_chapter_twist, novel_id, chapter_num)
    return {"result": result}


@router.post("/api/novels/{novel_id}/pacing-check/{chapter_num}")
async def check_pacing(novel_id: str, chapter_num: int, request: Request):
    """检查单章节奏质量"""
    _require_legacy_owner(request, novel_id)
    content = engine.get_chapter(novel_id, chapter_num)
    if content is None:
        raise HTTPException(status_code=404, detail=f"第{chapter_num}章不存在")

    # v3.8: 节奏分析含同步 LLM，放线程池避免阻塞事件循环
    import asyncio
    result = await asyncio.to_thread(engine.pacing_checker.analyze, content, chapter_num)
    return {"result": result}

