"""NovelGenerator — 爆火分析 / 逆天生成 API Router"""
import json
import os
from typing import Optional
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import traceback

router = APIRouter()

from ..deps import engine, log, _validate_novel_id, _validate_chapter_range
from core.trend_analyzer import TrendAnalyzer, BizarreNovelGenerator
from core.trend_analyzer import quick_bizarre, BizarreNovelGenerator

@router.post("/api/trends/analyze")
async def analyze_trends(req: dict = None):
    """抓取爆火小说并分析趋势"""
    if not req:
        req = {}
    try:
        from core.trend_analyzer import TrendAnalyzer, BizarreNovelGenerator

        analyzer = TrendAnalyzer(None, "")
        novels = analyzer.fetch_trending_novels(req.get("max_count", 20))
        patterns = analyzer.extract_patterns(novels)

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


@router.post("/api/trends/bizarre")
async def generate_bizarre(req: dict):
    """基于爆火趋势生成逆天小说（剧情反转向）"""
    count = min(req.get("count", 5), 10)
    style = req.get("style", "热血爽文")
    theme = req.get("theme", "")
    genre = req.get("genre", "")

    try:
        from core.trend_analyzer import TrendAnalyzer, BizarreNovelGenerator

        gen = BizarreNovelGenerator(None, "")
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
async def quick_bizarre(req: dict):
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
            news_items = gen.fetch_hot_news(10)

        novels = quick_bizarre(count, genre, mode, news_items=news_items)
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
async def create_bizarre_novel(req: dict):
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

        return {"ok": True, "title": title, "message": f"小说「{title}」创建成功"}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

