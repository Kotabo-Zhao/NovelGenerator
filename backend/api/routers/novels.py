"""NovelGenerator — 小说 CRUD / 生成 / 导出 / 批次 / 状态修复 API Router"""
import asyncio
import json
import os
import urllib.parse
from typing import Optional
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import NOVELS_DIR, DEFAULT_CHAPTER_WORDS

router = APIRouter()

from ..deps import engine, log, _validate_novel_id, _validate_chapter_range, _sse_with_heartbeat
from core.humanizer import humanize_text


class CreateNovelRequest(BaseModel):
    genre: str = "玄幻"
    style: str = "热血爽文"
    inspiration: str = ""
    target_words: int = 500000
    title: str = ""
    natural_names: bool = True  # 自然命名，去AI味
    normal_pacing: bool = False  # v2.2: 默认快节奏
    fast_food: bool = False  # v2.7: 快餐模式


class GenerateChapterRequest(BaseModel):
    novel_id: str
    chapter_num: int
    writing_mode: str = "webnovel"  # "webnovel" | "literary"
    feedback: Optional[str] = None  # 用户修改意见（重生成场景）


from ..deps import engine, log, _validate_novel_id, _validate_chapter_range, _sse_with_heartbeat
from core.humanizer import humanize_text

def _validate_novel_id(novel_id: str) -> str:
    """校验小说ID：只允许ASCII字母/数字/中文/下划线/连字符"""
    import re
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


@router.get("/api/novels")
async def list_novels():
    """列出所有小说"""
    return {"novels": engine.list_novels()}


@router.get("/api/novels/{novel_id}")
async def get_novel(novel_id: str):
    """获取小说详情"""
    plan = engine.get_novel(novel_id)
    if not plan:
        raise HTTPException(status_code=404, detail=f"小说 '{novel_id}' 不存在")
    # 移除过大的章节内容
    if "chapters" in plan:
        del plan["chapters"]
    return {"novel": plan}


@router.delete("/api/novels/{novel_id}")
async def delete_novel(novel_id: str):
    """删除小说及其所有章节、状态文件"""
    success = engine.delete_novel(novel_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"小说 '{novel_id}' 不存在或无法删除")
    return {"success": True, "deleted": novel_id}


@router.get("/api/novels/{novel_id}/quality-dashboard")
async def get_quality_dashboard(novel_id: str):
    """v2.16: 小说质量仪表板 — 聚合所有质量维度"""
    plan = engine.get_novel(novel_id)
    if not plan:
        raise HTTPException(status_code=404, detail=f"小说 '{novel_id}' 不存在")

    state = engine.memory.get_novel_state(novel_id)
    completed = state.get("completed_chapters", [])
    total = plan.get("outline", {}).get("total_chapters", 0)

    # 1. 完整性评分
    completeness = min(100, int(len(completed) / max(total, 1) * 100)) if total > 0 else 0

    # 2. AI痕迹评分（取最近一章）
    ai_score = None
    if completed:
        last_ch = max(completed)
        last_content = engine.get_chapter(novel_id, last_ch)
        if last_content and len(last_content) > 200:
            try:
                from core.humanizer import humanize_text
                h_result = humanize_text(last_content)
                ai_score = h_result["score"]
            except Exception:
                pass

    # 3. 章节字数统计
    word_counts = {}
    if completed:
        for ch in completed[-5:]:
            content = engine.get_chapter(novel_id, ch)
            if content:
                word_counts[str(ch)] = len(content)

    # 4. 伏笔统计
    foreshadow_ledger = plan.get("foreshadow_ledger", {})
    total_hooks = len(foreshadow_ledger)
    resolved_hooks = sum(1 for fs in foreshadow_ledger.values() if fs.get("actual_payoff_chapter"))

    # 5. 摘要数量（长篇连续性）
    summary_count = len(state.get("summaries", {}))

    return {
        "novel_id": novel_id,
        "title": plan.get("title", ""),
        "quality": {
            "completeness": {
                "score": completeness,
                "completed_chapters": len(completed),
                "total_chapters": total,
            },
            "ai_score": {
                "score": ai_score,
                "grade": "A" if ai_score and ai_score >= 80 else ("B" if ai_score and ai_score >= 60 else ("C" if ai_score and ai_score >= 40 else "N/A")),
                "note": "基于最近一章检测" if ai_score else "尚无已生成章节",
            },
            "chapter_word_counts": word_counts,
            "foreshadowing": {
                "total": total_hooks,
                "resolved": resolved_hooks,
                "rate": int(resolved_hooks / max(total_hooks, 1) * 100),
            },
            "continuity": {
                "auto_summaries": summary_count,
                "bridges": len(plan.get("coherence_report", {}).get("bridges", [])),
            },
        },
        "recommendations": _generate_quality_recommendations(
            completeness, ai_score, total_hooks, resolved_hooks, summary_count, total
        ),
    }


def _generate_quality_recommendations(completeness, ai_score, total_hooks, resolved_hooks,
                                      summary_count, total_chapters):
    """生成质量改进建议"""
    recs = []
    if completeness < 30:
        recs.append("📝 建议继续生成更多章节以完成故事")
    if ai_score and ai_score < 60:
        recs.append("🤖 最新一章AI痕迹较重，建议开启Humanizer重写")
    if total_hooks > 0 and resolved_hooks < total_hooks * 0.3:
        recs.append(f"🎯 伏笔回收率偏低({resolved_hooks}/{total_hooks})，建议在后续章节中回收伏笔")
    if total_chapters > 20 and summary_count < total_chapters * 0.5:
        recs.append("📚 长篇上下文不足，建议启用自动摘要功能以提升长篇连贯性")
    if not recs:
        recs.append("👍 当前质量良好，继续写作！")
    return recs


@router.get("/api/novels/{novel_id}/chapters/{chapter_num}")
async def get_chapter(novel_id: str, chapter_num: int):
    """读取单章正文"""
    content = engine.get_chapter(novel_id, chapter_num)
    if content is None:
        raise HTTPException(status_code=404, detail=f"第{chapter_num}章不存在")
    return {"content": content, "chapter_num": chapter_num}


@router.get("/api/novels/{novel_id}/chapters/{chapter_num}/exists")
async def chapter_exists(novel_id: str, chapter_num: int):
    """检查章节文件是否存在（避免前端切换空白）"""
    exists = engine.memory.chapter_exists(novel_id, chapter_num)
    return {"exists": exists, "chapter_num": chapter_num}


@router.post("/api/novels/{novel_id}/sync-state")
async def sync_novel_state(novel_id: str):
    """修复 state.json 与实际文件不同步的问题"""
    state = engine.memory.get_novel_state(novel_id)
    chapters = engine.memory.scan_chapters(novel_id)
    return {
        "state": state,
        "chapters_on_disk": chapters,
        "synced": state.get("completed_chapters") == chapters,
    }


@router.put("/api/novels/{novel_id}")
async def update_novel_plan(novel_id: str, plan_data: dict):
    """保存用户修改后的大纲"""
    try:
        success = engine.update_plan(novel_id, plan_data)
        if not success:
            raise HTTPException(status_code=404, detail=f"小说 '{novel_id}' 不存在")
        return {"success": True, "message": "大纲已保存"}
    except Exception as e:
        log.exception("Failed to update plan")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/novels")
async def create_novel(req: CreateNovelRequest):
    """创建新小说 — 灵感 → 世界观+角色+大纲（内部走流式避免 Render 超时）"""
    creative_input = {
        "genre": req.genre, "style": req.style,
        "inspiration": req.inspiration,
        "target_words": req.target_words, "title": req.title,
        "natural_names": req.natural_names,
        "normal_pacing": req.normal_pacing,
        "fast_food": req.fast_food,
    }
    try:
        plan = engine.create_novel(creative_input)
        return {"success": True, "novel": plan}
    except Exception as e:
        log.exception("Failed to create novel")
        # Fall through to streaming path if sync path failed (likely Render timeout)
        log.info("Retrying with streaming path...")
        plan = None
        async for event in engine.create_novel_stream(creative_input):
            if event.get("type") == "done":
                plan = event.get("plan")
            elif event.get("type") == "error":
                raise HTTPException(status_code=500, detail=event.get("message", "创建失败"))
        if plan:
            return {"success": True, "novel": plan}
        raise HTTPException(status_code=500, detail="创建失败，请重试")


@router.post("/api/novels/create-stream")
async def create_novel_stream(req: CreateNovelRequest):
    """流式创建新小说 — 带 Render 心跳防超时"""
    async def event_stream():
        async for data in _sse_with_heartbeat(
            engine.create_novel_stream({
                "genre": req.genre, "style": req.style,
                "inspiration": req.inspiration,
                "target_words": req.target_words,
                "title": req.title,
                "natural_names": req.natural_names,
                "normal_pacing": req.normal_pacing,
                "fast_food": req.fast_food,
            })
        ):
            yield data

    return StreamingResponse(event_stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/api/novels/generate")
async def generate_chapter(req: GenerateChapterRequest):
    """流式生成章节 (SSE + 心跳)"""
    async def event_stream():
        async for data in _sse_with_heartbeat(
            engine.generate_chapter_stream(
                req.novel_id, req.chapter_num, req.writing_mode,
                feedback=req.feedback,
            )
        ):
            yield data

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )


@router.post("/api/novels/generate/atomic")
async def generate_chapter_atomic(req: GenerateChapterRequest):
    """原子化生成章节 (逐beat独立LLM → 装配 → 评估)"""
    async def event_stream():
        async for data in _sse_with_heartbeat(
            engine.atomic_generate_chapter_stream(
                req.novel_id, req.chapter_num, req.writing_mode,
                feedback=req.feedback,
            )
        ):
            yield data

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )


@router.post("/api/novels/{novel_id}/generate/batch")
async def generate_batch(novel_id: str, req: dict):
    """批量生成章节 (SSE 流式进度 + 检查点断点续传)"""
    start = req.get("start_chapter", 1)
    end = req.get("end_chapter", 1)
    writing_mode = req.get("writing_mode", "webnovel")
    resume = req.get("resume", False)  # v2.14: 断点续传

    # v2.14: 断点续传 — 读取上次检查点，跳过已生成章节
    if resume:
        checkpoint = _read_batch_checkpoint(novel_id)
        if checkpoint:
            start = checkpoint.get("next_chapter", start)
            log.info(f"Resuming batch for {novel_id} from chapter {start} (checkpoint)")

    async def event_stream():
        try:
            failed = []
            for ch_num in range(start, end + 1):
                # v2.6: 流水线衔接 — 等上一章桥接任务落地（通常早已完成，0等待）
                if ch_num > start:
                    try:
                        await engine.await_pending_bridge(novel_id, ch_num - 1)
                    except Exception as bpe:
                        log.warning(f"Bridge await failed (non-fatal): {bpe}")

                # v2.14: 跳过已生成的章节（断点续传时）
                # v2.6: 防御 — 带"生成中"标记的残章不算完成，必须重新生成
                if resume and engine.memory.chapter_exists(novel_id, ch_num):
                    _existing = ""
                    try:
                        _existing = engine.memory.read_chapter(novel_id, ch_num) or ""
                    except Exception:
                        _existing = ""
                    if "生成中，尚未完成" in _existing or "<!-- 生成中" in _existing:
                        log.warning(f"Ch{ch_num} is an incomplete draft, regenerating")
                    else:
                        yield f"data: {json.dumps({'type':'chapter_skipped','chapter':ch_num,'message':'已存在，跳过'}, ensure_ascii=False)}\n\n"
                        continue

                yield f"data: {json.dumps({'type':'progress','chapter':ch_num,'total':end,'start':start}, ensure_ascii=False)}\n\n"
                chapter_error = None
                try:
                    async for event in engine.generate_chapter_stream(
                        novel_id, ch_num, writing_mode, batch_mode=True  # v2.6: 批量模式（9次LLM→1-3次）
                    ):
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                        if event.get("type") == "error":
                            chapter_error = event.get("message", "未知错误")
                    if not chapter_error:
                        yield f"data: {json.dumps({'type':'chapter_done','chapter':ch_num}, ensure_ascii=False)}\n\n"
                        # v2.14: 写入检查点
                        _save_batch_checkpoint(novel_id, ch_num + 1, end)
                except Exception as ch_err:
                    chapter_error = str(ch_err)
                    log.warning(f"Batch chapter {ch_num} exception: {ch_err}")

                if chapter_error:
                    failed.append(ch_num)
                    yield f"data: {json.dumps({'type':'chapter_failed','chapter':ch_num,'error':chapter_error}, ensure_ascii=False)}\n\n"

            # v2.14: 批量完成，清除检查点
            _clear_batch_checkpoint(novel_id)
            yield f"data: {json.dumps({'type':'batch_done','from':start,'to':end,'failed':failed}, ensure_ascii=False)}\n\n"
        except Exception as e:
            log.exception("batch generate crashed")
            # v2.14: 保留检查点，允许用户后续续传
            yield f"data: {json.dumps({'type':'error','message':f'❌ 批量生成中断：{e}。已生成章节已保存，可使用"断点续传"恢复。'}, ensure_ascii=False)}\n\n"

    # v2.6: 批量端点接入 SSE 心跳（5s ping，防止长生成连接被中间层掐断）
    return StreamingResponse(
        _sse_with_heartbeat(event_stream()),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


def _checkpoint_path(novel_id: str) -> str:
    """获取检查点文件路径"""
    import os
    novel_dir = os.path.join(config.NOVELS_DIR, novel_id)
    return os.path.join(novel_dir, ".batch_checkpoint.json")


def _save_batch_checkpoint(novel_id: str, next_chapter: int, end_chapter: int):
    """保存批量生成检查点"""
    import os, json, time as _time
    try:
        cp = {"novel_id": novel_id, "next_chapter": next_chapter,
              "end_chapter": end_chapter, "saved_at": _time.time()}
        path = _checkpoint_path(novel_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cp, f, ensure_ascii=False)
    except Exception as e:
        log.warning(f"Failed to save checkpoint: {e}")


def _read_batch_checkpoint(novel_id: str) -> dict:
    """读取批量生成检查点"""
    import os, json
    try:
        path = _checkpoint_path(novel_id)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return None


def _clear_batch_checkpoint(novel_id: str):
    """清除检查点"""
    import os
    try:
        path = _checkpoint_path(novel_id)
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


@router.get("/api/novels/{novel_id}/batch/checkpoint")
async def get_batch_checkpoint(novel_id: str):
    """检查是否有未完成的批量生成检查点"""
    cp = _read_batch_checkpoint(novel_id)
    if cp:
        return {"has_checkpoint": True, **cp}
    return {"has_checkpoint": False}


@router.get("/api/novels/{novel_id}/export")
async def export_novel(novel_id: str, fmt: str = "txt"):
    """导出单本小说 (txt / pdf / epub)"""
    if fmt == "pdf":
        return await export_novel_pdf(novel_id)
    if fmt == "epub":
        return await export_novel_epub(novel_id)

    content, err = engine.export_novel(novel_id, "txt")
    if err:
        raise HTTPException(status_code=404, detail=err)

    safe_name = novel_id.encode("ascii", errors="replace").decode().replace("?", "_") or "novel"
    return PlainTextResponse(
        content,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{urllib.parse.quote(novel_id)}.txt"}
    )


@router.post("/api/novels/export/batch")
async def batch_export(req: dict = None):
    """批量导出所有小说"""
    if req is None:
        req = {}
    novel_ids = req.get("novel_ids", [])
    fmt = req.get("fmt", "txt")

    if not novel_ids:
        # 导出全部
        novels = engine.list_novels()
        novel_ids = [n["id"] for n in novels]

    results = []
    for nid in novel_ids:
        content, err = engine.export_novel(nid, fmt)
        results.append({
            "novel_id": nid,
            "success": err is None,
            "error": err or None,
            "content": content if err is None else None,
        })

    return {"results": results}


@router.get("/api/novels/{novel_id}/character-bible")
async def get_character_bible(novel_id: str):
    """获取人物宝典"""
    bible_path = os.path.join(engine.memory.get_novel_dir(novel_id), "character_bible.json")
    if not os.path.exists(bible_path):
        raise HTTPException(status_code=404, detail="人物宝典尚未生成")

    with open(bible_path, "r", encoding="utf-8") as f:
        bible = json.load(f)
    return {"bible": bible}


@router.post("/api/repair-states")
async def repair_all_states():
    """修复所有小说的 state.json 一致性（以磁盘章节文件为准）"""
    results = engine.memory.repair_all_states()
    return {"repaired": len(results), "details": results}


@router.post("/api/novels/{novel_id}/repair-state")
async def repair_state(novel_id: str):
    """修复指定小说的 state.json"""
    result = engine.memory.repair_state(novel_id)
    return result

