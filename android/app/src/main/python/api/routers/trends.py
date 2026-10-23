"""NovelGenerator — 爆火分析 / 逆天生成 API Router"""
import json
import os
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import traceback

router = APIRouter()

from ..deps import engine, log, _validate_novel_id, _validate_chapter_range, ownership_store
from ..auth import require_owner
from core.trend_analyzer import TrendAnalyzer, BizarreNovelGenerator

@router.post("/api/trends/analyze")
async def analyze_trends(req: dict = None, _owner_id: str = Depends(require_owner)):
    """抓取爆火小说并分析趋势"""
    if not req:
        req = {}
    try:

        # v2.8.5: 传入真实 LLM client——此前恒传 None，deep_analyze 的
        # LLM 深度分析分支全部跳过，趋势分析退化为纯离线规则。
        analyzer = TrendAnalyzer(getattr(engine, "client", None), getattr(engine, "model", ""))
        # v3.8: 网络抓取 + 分析含同步调用，放线程池避免阻塞事件循环
        import asyncio
        novels, patterns = await asyncio.to_thread(
            _fetch_and_extract, analyzer, req.get("max_count", 20)
        )

        return {
            "ok": True,
            "hot_novels": [{"title": n["title"], "author": n["author"],
                          "genre": n["genre"], "tags": n.get("tags", []),
                          "popularity": n.get("popularity", 0)} for n in novels[:15]],
            "patterns": patterns,
            "source": "builtin" if not novels or all(n.get("source") != "qidian" for n in novels) else "live",
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _fetch_and_extract(analyzer, max_count: int):
    """同步抓取+分析，供 asyncio.to_thread 调用（避免在 async handler 内阻塞事件循环）"""
    novels = analyzer.fetch_trending_novels(max_count)
    patterns = analyzer.extract_patterns(novels)
    return novels, patterns


@router.post("/api/trends/bizarre")
async def generate_bizarre(req: dict, _owner_id: str = Depends(require_owner)):
    """基于爆火趋势生成逆天小说（剧情反转向）"""
    count = min(req.get("count", 5), 10)
    style = req.get("style", "热血爽文")
    theme = req.get("theme", "")
    genre = req.get("genre", "")

    try:

        # v2.8.5: 传入真实 LLM client，启用 LLM 增强的逆天生成分支
        gen = BizarreNovelGenerator(getattr(engine, "client", None), getattr(engine, "model", ""))
        result = await gen.full_pipeline(count, style, theme, genre)

        return {
            "ok": True,
            "trends": result["trends"],
            "hot_novels": result["hot_novels"],
            "bizarre_novels": [
                {
                    "premise": bn["premise"],
                    "outline": bn.get("outline"),
                }
                for bn in result["bizarre_novels"]
            ],
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"ok": False, "error": str(e)}


@router.post("/api/trends/bizarre/quick")
async def quick_bizarre(req: dict, _owner_id: str = Depends(require_owner)):
    """快速生成逆天小说设定（时事模式优先从网络抓热点新闻动态生成）"""
    count = min(req.get("count", 3), 5)
    genre = req.get("genre", "")
    mode = req.get("mode", "topical")

    try:
        from core.trend_analyzer import quick_bizarre, BizarreNovelGenerator

        # 时事模式: 先抓热点新闻
        news_items = None
        if mode in ("topical", "mixed"):
            gen = BizarreNovelGenerator()
            # v3.8: 网络抓取放线程池避免阻塞事件循环
            import asyncio
            news_items = await asyncio.to_thread(gen.fetch_hot_news, 10)

        # v3.8: 生成逻辑含同步 LLM，放线程池避免阻塞事件循环
        novels = await asyncio.to_thread(quick_bizarre, count, genre, mode, news_items=news_items)
        return {
            "ok": True,
            "news_count": len(news_items) if news_items else 0,
            "bizarre_novels": [
                {
                    "premise": n["premise"],
                }
                for n in novels
            ],
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/api/trends/bizarre/create")
async def create_bizarre_novel(req: dict, _owner_id: str = Depends(require_owner)):
    """将剧情反转种子直接创建为小说"""
    if not req or not req.get("premise"):
        raise HTTPException(status_code=400, detail="缺少 premise")

    try:
        premise = req["premise"]
        title = premise.get("title", "逆天小说")

        # engine 来自 deps 单例

        if engine.memory.novel_exists(title):
            raise HTTPException(status_code=409, detail=f"小说「{title}」已存在")

        # 用剧情梗概作为灵感
        synopsis = premise.get("synopsis", premise.get("one_liner", ""))
        inspiration = f"【剧情反转种子·{premise.get('tag', '')}】{synopsis}"

        # 复用现有流式创建管线（含需求拆解 Phase 0），替代不存在的 NovelPlanner
        plan = engine.create_novel_stream({
            "genre": premise.get("genre", "玄幻").split("+")[0],
            "style": req.get("style", "热血爽文"),
            "inspiration": inspiration,
            "title": title,
            "target_words": 50000,
            "natural_names": True,
        })

        async for event in plan:
            if event.get("type") == "error":
                raise HTTPException(status_code=500, detail=event.get("message", "创建失败"))
            if event.get("type") == "done" and event.get("plan", {}).get("title"):
                # v3.8: 创建成功即绑定归属，避免孤儿作品（此前从未 claim，作品不出现在用户书架）
                ownership_store.claim(event["plan"]["title"], _owner_id)

        return {"ok": True, "title": title, "message": f"小说「{title}」创建成功"}
    except Exception as e:
        import traceback
        traceback.print_exc()
        # v3.8: 异常详情只进日志，客户端收通用文案
        raise HTTPException(status_code=500, detail="小说创建失败，请稍后重试") from e

