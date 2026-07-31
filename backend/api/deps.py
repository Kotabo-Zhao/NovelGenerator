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
from config import NOVELS_DIR

log = logging.getLogger("api")

# ── 全局单例（模块加载时实例化一次，多 router 共享）──
engine = NovelEngine()

# 保证 novels 目录存在
os.makedirs(NOVELS_DIR, exist_ok=True)


def _validate_novel_id(novel_id: str) -> str:
    """校验小说ID：只允许ASCII字母/数字/中文/下划线/连字符"""
    if not novel_id or not isinstance(novel_id, str):
        raise HTTPException(400, "❌ 小说ID不能为空")
    if len(novel_id) > 200:
        raise HTTPException(400, "❌ 小说ID过长（最多200字符）")
    if re.search(r'[<>"/\\|?*]', novel_id):
        raise HTTPException(400, "❌ 小说ID包含非法字符")
    # 反路径遍历
    if ".." in novel_id:
        raise HTTPException(400, "❌ 小说ID包含非法路径序列")
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
    """通用心跳包装: 每5s发送ping防止超时断开SSE (v2.14: 无上限，跟随producer生命周期)"""
    q = asyncio.Queue()
    cancelled = False

    async def producer():
        try:
            async for event in event_generator:
                # 防御: 确保event是dict
                if not isinstance(event, dict):
                    log.error(f"SSE producer got non-dict event: {type(event).__name__}: {str(event)[:200]}")
                    event = {"type": "warning", "message": f"内部数据格式异常: {type(event).__name__}"}
                await q.put(("event", event))
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            log.exception("SSE producer crashed")
            # v2.10: 返回异常类型以便快速定位
            err_msg = f"生成过程出错 [{type(e).__name__}]: {str(e)[:300]}"
            log.error(f"SSE crash details:\n{tb}")
            await q.put(("error", err_msg))
        await q.put(("done", None))

    async def heartbeater():
        t = 0
        while not cancelled:
            await asyncio.sleep(5)  # v2.14: 5秒间隔(原来8秒)，更积极保活
            if cancelled:
                break
            t += 1
            await q.put(("ping", {"type":"ping","t":t}))

    p_task = asyncio.create_task(producer())
    h_task = asyncio.create_task(heartbeater())

    try:
        while True:
            kind, data = await q.get()
            if kind == "done":
                cancelled = True; h_task.cancel(); break
            elif kind == "event":
                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
            elif kind == "ping":
                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
            elif kind == "error":
                yield f"data: {json.dumps({'type':'error','message':f'生成过程出错: {data}'}, ensure_ascii=False)}\n\n"
                cancelled = True; h_task.cancel(); break
    finally:
        h_task.cancel()
        if not p_task.done():
            p_task.cancel()
        try:
            await p_task
        except asyncio.CancelledError:
            pass
