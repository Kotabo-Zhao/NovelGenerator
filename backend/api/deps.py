"""NovelGenerator — API 共享依赖（engine 单例 + 通用校验工具）

server.py 拆分后，各域 router 从这里获取共享的 engine 实例与校验工具。
Python 模块缓存保证 engine 只实例化一次。
"""
import asyncio
import json
import logging
import os
import re

from fastapi import HTTPException

from core.engine import NovelEngine
from core.paths import novel_path, InvalidNovelPathError
from config import NOVELS_DIR

log = logging.getLogger("api")

# ── 全局单例（模块加载时实例化一次，多 router 共享）──
engine = NovelEngine()

# 保证 novels 目录存在
os.makedirs(NOVELS_DIR, exist_ok=True)


def _validate_novel_id(novel_id: str) -> str:
    try:
        novel_path(NOVELS_DIR, novel_id)
    except InvalidNovelPathError as exc:
        raise HTTPException(400, str(exc)) from None
    return novel_id


def _validate_chapter_range(start: int, end: int):
    """校验章节范围"""
    if not isinstance(start, int) or not isinstance(end, int):
        raise HTTPException(400, "❌ 章节号必须为整数")
    if start < 1 or end < 1:
        raise HTTPException(400, "❌ 章节号必须大于0")
    if start > end:
        raise HTTPException(400, f"❌ 起始章节({start})不能大于结束章节({end})")
    if end - start > 200:
        raise HTTPException(400, "❌ 批量生成一次最多200章")


# ── SSE 心跳包装（novels/outline/requirements 共用）──
async def _sse_with_heartbeat(event_generator):
    """Bounded backpressure, heartbeat, and cancellation of the producer."""
    q = asyncio.Queue(maxsize=64)

    async def producer():
        try:
            async for event in event_generator:
                if not isinstance(event, dict):
                    raise TypeError("SSE producer must yield event dictionaries")
                await q.put(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("SSE producer failed")
            await q.put({"type": "error", "message": "生成过程出错，请重试或查看服务日志"})
        await q.put(None)

    p_task = asyncio.create_task(producer())
    try:
        while True:
            try:
                data = await asyncio.wait_for(q.get(), timeout=5)
            except asyncio.TimeoutError:
                yield ': heartbeat\n\n'
                continue
            if data is None:
                break
            yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    finally:
        if not p_task.done():
            p_task.cancel()
        await asyncio.gather(p_task, return_exceptions=True)
        if hasattr(event_generator, "aclose"):
            await event_generator.aclose()
