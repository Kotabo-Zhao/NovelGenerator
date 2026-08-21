"""NovelGenerator — 小说 CRUD / 生成 / 导出 / 批次 / 状态修复 API Router"""
import asyncio
import json
import os
import urllib.parse
from typing import List, Literal, Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse, PlainTextResponse
from pydantic import BaseModel, Field, validator

from core.atomic_io import atomic_write_json, safe_read_json
from core.path_security import validate_novel_id as _validate_path_novel_id

router = APIRouter()

from ..deps import engine, log, ownership_store, _validate_novel_id, _validate_chapter_range, _sse_with_heartbeat
from ..auth import require_owner, is_internal_version
from core.humanizer import humanize_text


class CreateNovelRequest(BaseModel):
    genre: str = Field("玄幻", min_length=1, max_length=40)
    style: str = Field("热血爽文", min_length=1, max_length=100)
    inspiration: str = Field("", max_length=20000)
    target_words: int = Field(50000, ge=100, le=2000000)
    title: str = Field("", max_length=100)
    natural_names: bool = True  # 自然命名，去AI味
    normal_pacing: bool = False  # v2.2: 默认快节奏
    fast_food: bool = False  # v2.7: 快餐模式
    creative_enhance: bool = False  # v3.5.26: 创意加成（默认关——忠实执行用户需求，不加私货）


class GenerateChapterRequest(BaseModel):
    novel_id: str = Field(..., min_length=1, max_length=200)
    chapter_num: int = Field(..., ge=1, le=100000)
    writing_mode: Literal["webnovel", "literary"] = "webnovel"
    feedback: Optional[str] = Field(None, max_length=5000)

    @validator("novel_id")
    def validate_novel_id(cls, value):
        try:
            return _validate_path_novel_id(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


class BatchGenerationRequest(BaseModel):
    start_chapter: int = Field(1, ge=1, le=100000)
    end_chapter: int = Field(1, ge=1, le=100000)
    writing_mode: Literal["webnovel", "literary"] = "webnovel"
    resume: bool = False


class BatchExportRequest(BaseModel):
    novel_ids: List[str] = Field(default_factory=list, max_items=50)
    fmt: Literal["txt", "pdf", "epub"] = "txt"

    @validator("novel_ids", each_item=True)
    def validate_export_novel_id(cls, value):
        try:
            return _validate_path_novel_id(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

def _require_legacy_owner(request: Request, novel_id: str) -> str:
    """Return the owner installed by the legacy compatibility middleware."""
    owner_id = getattr(request.state, "owner_id", None)
    if not owner_id or (not is_internal_version() and not ownership_store.owns(novel_id, owner_id)):
        raise HTTPException(status_code=404, detail="小说不存在")
    return owner_id


@router.get("/api/novels")
async def list_novels(request: Request):
    """列出当前登录用户拥有的小说，兼容旧响应格式。"""
    owner_id = getattr(request.state, "owner_id", None)
    if not owner_id and not is_internal_version():
        raise HTTPException(status_code=401, detail="需要微信登录")
    if is_internal_version():
        return {"novels": engine.list_novels()}
    owned_ids = {item["novel_id"] for item in ownership_store.list_for_owner(owner_id)}
    return {"novels": [novel for novel in engine.list_novels() if novel.get("id") in owned_ids]}


@router.get("/api/novels/{novel_id}")
async def get_novel(novel_id: str, request: Request):
    """获取小说详情"""
    _require_legacy_owner(request, novel_id)
    plan = engine.get_novel(novel_id)
    if not plan:
        raise HTTPException(status_code=404, detail=f"小说 '{novel_id}' 不存在")
    # 移除过大的章节内容
    if "chapters" in plan:
        del plan["chapters"]
    return {"novel": plan}


@router.delete("/api/novels/{novel_id}")
async def delete_novel(novel_id: str, request: Request):
    """删除小说及其所有章节、状态文件"""
    owner_id = _require_legacy_owner(request, novel_id)
    success = engine.delete_novel(novel_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"小说 '{novel_id}' 不存在或无法删除")
    ownership_store.mark_deleted(novel_id, owner_id)
    return {"success": True, "deleted": novel_id}


@router.get("/api/novels/{novel_id}/quality-dashboard")
async def get_quality_dashboard(novel_id: str, request: Request):
    """v2.16: 小说质量仪表板 — 聚合所有质量维度"""
    _require_legacy_owner(request, novel_id)
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

    # 4. 伏笔统计（含逐条账本——前端"伏笔账本"面板数据源）
    foreshadow_ledger = plan.get("foreshadow_ledger", {})
    total_hooks = len(foreshadow_ledger)
    resolved_hooks = sum(1 for fs in foreshadow_ledger.values() if fs.get("actual_payoff_chapter"))
    foreshadow_list = []
    try:
        _hooks = engine.memory.read("foreshadowing", novel_id)
        if not isinstance(_hooks, list) or not _hooks:
            # fallback: 部分小说只把伏笔写在 plan.json 的 foreshadow_ledger
            _hooks = [
                {"description": str(fs.get("description", ""))[:120],
                 "plant_chapter": fs.get("plant_chapter"),
                 "actual_payoff_chapter": fs.get("actual_payoff_chapter")}
                for fs in foreshadow_ledger.values()
                if isinstance(fs, dict) and fs.get("description")
            ]
        if isinstance(_hooks, list):
            foreshadow_list = [
                {
                    "description": str(h.get("description", ""))[:120],
                    "plant_chapter": h.get("plant_chapter"),
                    "reveal_chapter": h.get("reveal_chapter"),
                    "actual_payoff_chapter": h.get("actual_payoff_chapter"),
                }
                for h in _hooks[:20]
                if isinstance(h, dict) and h.get("description")
            ]
    except Exception:
        pass  # 伏笔文件缺失/损坏时账本为空，不阻断质量面板

    # 5. 摘要数量（长篇连续性）——摘要写入 global_state.json（generation.py
    #    chapter_summarizer 均写 global_state），从 state.json 读恒为 0。
    try:
        _gs = engine.memory.read("global_state", novel_id, skip_cache=True)
        summary_count = len((_gs or {}).get("chapters_summary", {}))
    except Exception:
        summary_count = 0

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
                "list": foreshadow_list,
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
async def get_chapter(novel_id: str, chapter_num: int, request: Request):
    """读取单章正文与独立修订草稿。"""
    _require_legacy_owner(request, novel_id)
    content = engine.get_chapter(novel_id, chapter_num)
    draft = engine.memory.read_chapter_draft(novel_id, chapter_num)
    if content is None and draft is None:
        raise HTTPException(status_code=404, detail=f"第{chapter_num}章不存在")
    return {
        "content": content,
        "draft": draft,
        "is_published": content is not None,
        "chapter_num": chapter_num,
    }


@router.get("/api/novels/{novel_id}/chapters/{chapter_num}/exists")
async def chapter_exists(novel_id: str, chapter_num: int, request: Request):
    """检查章节文件是否存在（避免前端切换空白）"""
    _require_legacy_owner(request, novel_id)
    exists = engine.memory.chapter_exists(novel_id, chapter_num)
    return {"exists": exists, "chapter_num": chapter_num}


@router.post("/api/novels/{novel_id}/sync-state")
async def sync_novel_state(novel_id: str, request: Request):
    """修复 state.json 与实际文件不同步的问题"""
    _require_legacy_owner(request, novel_id)
    state = engine.memory.get_novel_state(novel_id)
    chapters = engine.memory.scan_chapters(novel_id)
    return {
        "state": state,
        "chapters_on_disk": chapters,
        "synced": state.get("completed_chapters") == chapters,
    }


@router.put("/api/novels/{novel_id}")
async def update_novel_plan(novel_id: str, plan_data: dict, request: Request):
    """保存用户修改后的大纲"""
    _require_legacy_owner(request, novel_id)
    try:
        success = engine.update_plan(novel_id, plan_data)
        if not success:
            raise HTTPException(status_code=404, detail=f"小说 '{novel_id}' 不存在")
        return {"success": True, "message": "大纲已保存"}
    except HTTPException:
        raise
    except Exception as e:
        # v3.8: 内部异常细节只进日志，不回传客户端（防内部信息泄漏）
        log.exception("Failed to update plan")
        raise HTTPException(status_code=500, detail="大纲保存失败，请稍后重试") from e


@router.post("/api/novels")
async def create_novel(req: CreateNovelRequest, request: Request):
    """创建新小说 — 灵感 → 世界观+角色+大纲（内部走流式避免 Render 超时）"""
    creative_input = {
        "genre": req.genre, "style": req.style,
        "inspiration": req.inspiration,
        "target_words": req.target_words, "title": req.title,
        "natural_names": req.natural_names,
        "normal_pacing": req.normal_pacing,
        "fast_food": req.fast_food,
    }
    plan = None
    try:
        plan = engine.create_novel(creative_input)
    except Exception as e:
        log.exception("Failed to create novel")
        # Fall through to streaming path if sync path failed (likely Render timeout)
        log.info("Retrying with streaming path...")
        async for event in engine.create_novel_stream(creative_input):
            if event.get("type") == "done":
                plan = event.get("plan")
            elif event.get("type") == "error":
                raise HTTPException(status_code=500, detail=event.get("message", "创建失败"))
    if not plan:
        raise HTTPException(status_code=500, detail="创建失败，请重试")
    owner_id = getattr(request.state, "owner_id", None)
    if not owner_id and not is_internal_version():
        raise HTTPException(status_code=401, detail="需要微信登录")
    if not owner_id or not plan.get("title"):
        raise HTTPException(status_code=500, detail="登录用户或作品 ID 缺失")
    try:
        ownership_store.claim(plan["title"], owner_id)
    except Exception as exc:
        log.exception("Failed to claim created novel")
        raise HTTPException(status_code=500, detail="作品归属登记失败，请重试") from exc
    return {"success": True, "novel": plan}


@router.post("/api/novels/create-stream")
async def create_novel_stream(req: CreateNovelRequest, request: Request):
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
            if data.startswith("data: "):
                try:
                    event = json.loads(data[6:].strip())
                    if event.get("type") == "done" and event.get("plan", {}).get("title"):
                        owner_id = getattr(request.state, "owner_id", None)
                        if not owner_id:
                            raise RuntimeError("登录用户缺失")
                        ownership_store.claim(event["plan"]["title"], owner_id)
                except json.JSONDecodeError:
                    pass
            yield data

    return StreamingResponse(event_stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/api/novels/generate")
async def generate_chapter(req: GenerateChapterRequest, request: Request):
    """流式生成章节 (SSE + 心跳)"""
    _require_legacy_owner(request, req.novel_id)
    async def event_stream():
        async for data in _sse_with_heartbeat(
            engine.unified_generate_chapter_stream(
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
async def generate_chapter_atomic(req: GenerateChapterRequest, request: Request):
    """原子化生成章节 (逐beat独立LLM → 装配 → 评估)"""
    _require_legacy_owner(request, req.novel_id)
    async def event_stream():
        async for data in _sse_with_heartbeat(
            engine.unified_generate_chapter_stream(
                req.novel_id, req.chapter_num, req.writing_mode,
                feedback=req.feedback,
                force_strategy="atomic",
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
async def generate_batch(novel_id: str, req: BatchGenerationRequest, request: Request):
    """批量生成章节 (SSE 流式进度 + 检查点断点续传)"""
    _require_legacy_owner(request, novel_id)
    _validate_novel_id(novel_id)
    start = req.start_chapter
    end = req.end_chapter
    writing_mode = req.writing_mode
    resume = req.resume  # v2.14: 断点续传
    _validate_chapter_range(start, end)

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
                        # v3.8: 统一 yield dict，由 _sse_with_heartbeat 格式化（此前预序列化字符串被包装器替换为警告）
                        yield {"type": "chapter_skipped", "chapter": ch_num, "message": "已存在，跳过"}
                        continue

                yield {"type": "progress", "chapter": ch_num, "total": end, "start": start}
                chapter_error = None
                try:
                    async for event in engine.unified_generate_chapter_stream(
                        novel_id, ch_num, writing_mode, batch_mode=True  # v2.6: 批量模式（9次LLM→1-3次）
                    ):
                        yield event
                        if event.get("type") in {"error", "needs_revision"}:
                            chapter_error = event.get("message", "未知错误")
                    if not chapter_error:
                        yield {"type": "chapter_done", "chapter": ch_num}
                        # v2.14: 写入检查点
                        _save_batch_checkpoint(novel_id, ch_num + 1, end)
                except Exception as ch_err:
                    # v3.8: 异常详情只进日志，客户端收通用文案
                    chapter_error = f"第{ch_num}章生成失败"
                    log.warning(f"Batch chapter {ch_num} exception: {ch_err}")

                if chapter_error:
                    failed.append(ch_num)
                    yield {"type": "chapter_failed", "chapter": ch_num, "error": chapter_error}

            # v2.14: 批量完成，清除检查点
            _clear_batch_checkpoint(novel_id)
            yield {"type": "batch_done", "from": start, "to": end, "failed": failed}
        except Exception as e:
            log.exception("batch generate crashed")
            # v2.14: 保留检查点，允许用户后续续传；v3.8: 内部异常不回传
            yield {"type": "error", "message": "❌ 批量生成中断。已生成章节已保存，可使用“断点续传”恢复。"}

    # v2.6: 批量端点接入 SSE 心跳（5s ping，防止长生成连接被中间层掐断）
    return StreamingResponse(
        _sse_with_heartbeat(event_stream()),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


def _checkpoint_path(novel_id: str) -> str:
    """获取检查点文件路径"""
    _validate_novel_id(novel_id)
    return os.path.join(engine.memory.get_novel_dir(novel_id), ".batch_checkpoint.json")


def _save_batch_checkpoint(novel_id: str, next_chapter: int, end_chapter: int):
    """保存批量生成检查点"""
    import time as _time
    try:
        cp = {"novel_id": novel_id, "next_chapter": next_chapter,
              "end_chapter": end_chapter, "saved_at": _time.time()}
        path = _checkpoint_path(novel_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        atomic_write_json(path, cp)
    except Exception as e:
        log.warning(f"Failed to save checkpoint: {e}")


def _read_batch_checkpoint(novel_id: str) -> dict:
    """读取批量生成检查点"""
    try:
        path = _checkpoint_path(novel_id)
        if os.path.exists(path):
            checkpoint = safe_read_json(path, None)
            if isinstance(checkpoint, dict) and checkpoint.get("novel_id") == novel_id:
                return checkpoint
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
async def get_batch_checkpoint(novel_id: str, request: Request):
    """检查是否有未完成的批量生成检查点"""
    _require_legacy_owner(request, novel_id)
    cp = _read_batch_checkpoint(novel_id)
    if cp:
        return {"has_checkpoint": True, **cp}
    return {"has_checkpoint": False}


@router.get("/api/novels/{novel_id}/export")
async def export_novel(novel_id: str, request: Request, fmt: str = "txt"):
    """导出单本小说 (txt / pdf / epub)"""
    _require_legacy_owner(request, novel_id)
    if fmt not in {"txt", "epub"}:
        raise HTTPException(status_code=400, detail="当前仅支持 txt 和 epub 导出")
    content, err = await asyncio.to_thread(engine.export_novel, novel_id, fmt)
    if err:
        raise HTTPException(status_code=404, detail=err)

    suffix = "epub" if fmt == "epub" else "txt"
    media_type = "application/epub+zip" if suffix == "epub" else "text/plain; charset=utf-8"
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{urllib.parse.quote(novel_id)}.{suffix}"}
    if isinstance(content, bytes):
        return Response(content, media_type=media_type, headers=headers)
    return PlainTextResponse(content, media_type=media_type, headers=headers)


@router.post("/api/novels/export/batch")
async def batch_export(req: BatchExportRequest, request: Request):
    """批量导出多本小说（legacy 兼容面：web 前端 internal 模式使用）。

    v1 的 /v1/novels/{novel_id}/exports 走认证 job 队列（小程序）；
    本接口保留同步批量导出供 web（无登录态）使用，避免 web 端 410 后功能失效。
    """
    novel_ids = req.novel_ids
    fmt = req.fmt

    if not novel_ids:
        # 导出全部
        owner_id = getattr(request.state, "owner_id", None)
        if owner_id:
            novel_ids = [item["novel_id"] for item in ownership_store.list_for_owner(owner_id)]
        else:
            novel_ids = [n.get("id") for n in engine.list_novels() if n.get("id")]

    for novel_id in novel_ids:
        _require_legacy_owner(request, novel_id)

    results = []
    for nid in novel_ids:
        content, err = await asyncio.to_thread(engine.export_novel, nid, fmt)
        results.append({
            "novel_id": nid,
            "success": err is None,
            "error": err or None,
            "content": content if err is None else None,
        })

    return {"results": results}


@router.get("/api/novels/{novel_id}/character-bible")
async def get_character_bible(novel_id: str, request: Request):
    """获取人物宝典"""
    _require_legacy_owner(request, novel_id)
    bible_path = os.path.join(engine.memory.get_novel_dir(novel_id), "character_bible.json")
    if not os.path.exists(bible_path):
        raise HTTPException(status_code=404, detail="人物宝典尚未生成")

    with open(bible_path, "r", encoding="utf-8") as f:
        bible = json.load(f)
    return {"bible": bible}


@router.post("/api/repair-states")
async def repair_all_states(_owner_id: str = Depends(require_owner)):
    """修复所有小说的 state.json 一致性（以磁盘章节文件为准）"""
    results = engine.memory.repair_all_states()
    return {"repaired": len(results), "details": results}


@router.post("/api/novels/{novel_id}/repair-state")
async def repair_state(novel_id: str, request: Request):
    """修复指定小说的 state.json"""
    _require_legacy_owner(request, novel_id)
    result = engine.memory.repair_state(novel_id)
    return result


# ── v2.8 创作者工具：全文检索 ──

@router.get("/api/novels/{novel_id}/search")
async def search_novel(novel_id: str, q: str, request: Request, limit: int = 50):
    """全文搜索：章节正文 + 大纲 + 角色卡。

    q: 关键词（大小写不敏感子串匹配，中文直接子串）
    limit: 结果上限（默认 50）
    返回按 source 分组的命中列表，snippet 为命中句前后各 40 字符。
    """
    _require_legacy_owner(request, novel_id)
    q = (q or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="请输入搜索关键词")
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit 必须在 1-200 之间")

    ql = q.lower()
    results = []

    # 1) 章节正文
    novel_dir = engine.memory.get_novel_dir(novel_id)
    chapters_dir = os.path.join(novel_dir, "chapters")
    if os.path.isdir(chapters_dir):
        for fname in sorted(os.listdir(chapters_dir)):
            if not fname.endswith(".md"):
                continue
            fpath = os.path.join(chapters_dir, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    text = f.read()
            except Exception:
                continue
            if ql not in text.lower():
                continue
            try:
                ch_num = int("".join(ch for ch in fname if ch.isdigit()) or "0")
            except Exception:
                ch_num = 0
            # 找到所有命中行，取前 2 个做 snippet
            hit_lines = []
            for line in text.splitlines():
                if ql in line.lower():
                    hit_lines.append(line.strip()[:120])
                    if len(hit_lines) >= 2:
                        break
            if hit_lines:
                results.append({
                    "source": "chapter", "chapter_num": ch_num,
                    "title": f"第{ch_num}章",
                    "snippet": "…".join(hit_lines),
                })
            if len(results) >= limit:
                break

    # 2) 大纲（plan.json 的 title/summary/hook/opening_scene/beats）
    if len(results) < limit:
        try:
            plan = engine.get_novel(novel_id) or {}
            for vol in (plan.get("outline", {}) or {}).get("volumes", []) or []:
                for ch in (vol.get("chapters", []) or []):
                    if not isinstance(ch, dict):
                        continue
                    hay = " ".join(str(ch.get(k, "")) for k in
                                   ("title", "summary", "hook", "opening_scene", "bridge_to_next", "emotion_curve"))
                    if ql in hay.lower():
                        results.append({
                            "source": "outline", "chapter_num": int(ch.get("number", 0)),
                            "title": str(ch.get("title", "")),
                            "snippet": str(ch.get("summary", ""))[:120],
                        })
                        if len(results) >= limit:
                            break
        except Exception:
            pass

    # 3) 角色卡（character_bible.json）
    if len(results) < limit:
        bible_path = os.path.join(novel_dir, "character_bible.json")
        if os.path.exists(bible_path):
            try:
                with open(bible_path, "r", encoding="utf-8") as f:
                    bible = json.load(f)
                for name, prof in (bible or {}).items():
                    if not isinstance(prof, dict):
                        continue
                    hay = " ".join(str(v) for v in prof.values())
                    if ql in hay.lower():
                        results.append({
                            "source": "bible", "chapter_num": 0,
                            "title": str(name),
                            "snippet": hay[:120],
                        })
                        if len(results) >= limit:
                            break
            except Exception:
                pass

    return {"query": q, "count": len(results), "results": results}


# ── v2.8 创作者工具：章节精细操作（续写/扩写/精简/重写/拆分/合并）──

class SurgicalEditRequest(BaseModel):
    op: Literal["continue", "expand", "condense", "rewrite", "split", "merge"]
    selection: Optional[dict] = None          # {start, end} 字符位置（可选）
    instruction: Optional[str] = None         # rewrite/expand 用户意见
    merge_with: Optional[int] = None          # merge 目标章号
    target_words: Optional[int] = None        # 续写目标字数

    @validator("op")
    def validate_op(cls, v):
        if v not in ("continue", "expand", "condense", "rewrite", "split", "merge"):
            raise ValueError("未知操作类型")
        return v


@router.post("/api/novels/{novel_id}/chapters/{chapter_num}/surgical")
async def surgical_edit(novel_id: str, chapter_num: int, req: SurgicalEditRequest, request: Request):
    """章节精细化操作：续写/扩写/精简/重写/拆分/合并。

    所有修改前自动快照当前版到 .versions/（最多 5 版）。
    """
    _require_legacy_owner(request, novel_id)
    novel_dir = engine.memory.get_novel_dir(novel_id)
    chapters_dir = os.path.join(novel_dir, "chapters")
    os.makedirs(chapters_dir, exist_ok=True)

    def _read_ch(fnum):
        fp = os.path.join(chapters_dir, f"chapter_{fnum:04d}.md")
        if not os.path.exists(fp):
            return None
        with open(fp, "r", encoding="utf-8") as f:
            return f.read()

    def _write_ch(fnum, content):
        fp = os.path.join(chapters_dir, f"chapter_{fnum:04d}.md")
        # 版本快照（写入前保存旧版）——必须先读磁盘旧内容再快照，
        # 否则快照的是"即将写入的新内容"，回滚到 v1 等于没回滚。
        previous = None
        if os.path.exists(fp):
            with open(fp, "r", encoding="utf-8") as f:
                previous = f.read()
        _snapshot_chapter(fnum, previous)
        with open(fp, "w", encoding="utf-8") as f:
            f.write(content)

    def _snapshot_chapter(fnum, current_content):
        """保存当前版到 .versions/，最多 5 版滚动。"""
        try:
            vdir = os.path.join(novel_dir, ".versions")
            os.makedirs(vdir, exist_ok=True)
            # 已有版本文件 v1..v5；新快照前先移位
            import shutil
            for i in range(4, 0, -1):
                src = os.path.join(vdir, f"chapter_{fnum:04d}.v{i}.md")
                if os.path.exists(src):
                    shutil.copy(src, os.path.join(vdir, f"chapter_{fnum:04d}.v{i+1}.md"))
            v1 = os.path.join(vdir, f"chapter_{fnum:04d}.v1.md")
            with open(v1, "w", encoding="utf-8") as f:
                f.write(current_content or "")
            # 清理 v6+
            for i in range(6, 12):
                stale = os.path.join(vdir, f"chapter_{fnum:04d}.v{i}.md")
                if os.path.exists(stale):
                    os.remove(stale)
        except Exception as e:
            log.warning(f"Chapter snapshot failed (non-fatal): {e}")

    content = _read_ch(chapter_num)
    if content is None:
        raise HTTPException(status_code=404, detail=f"第{chapter_num}章不存在")

    op = req.op

    # ── split：按段落拆成两章 ──
    if op == "split":
        paras = [p.strip() for p in content.split("\n\n") if p.strip()]
        if len(paras) < 4:
            raise HTTPException(status_code=400, detail="章节太短，无法拆分（至少 4 段）")
        mid = len(paras) // 2
        part1 = "\n\n".join(paras[:mid])
        part2 = "\n\n".join(paras[mid:])
        # 新章写入 chapter_{num+1}，后续章节文件后移
        next_num = chapter_num + 1
        # 先把 num 之后的所有章节文件后移（从后往前）
        existing = sorted(
            int(f[8:12]) for f in os.listdir(chapters_dir) if f.startswith("chapter_") and f.endswith(".md")
        )
        for fnum in sorted([e for e in existing if e >= next_num], reverse=True):
            src = os.path.join(chapters_dir, f"chapter_{fnum:04d}.md")
            dst = os.path.join(chapters_dir, f"chapter_{fnum+1:04d}.md")
            if os.path.exists(src) and not os.path.exists(dst):
                os.rename(src, dst)
        _write_ch(chapter_num, part1)
        _write_ch(next_num, part2)
        # 更新 plan.json 编号
        try:
            plan = engine.get_novel(novel_id) or {}
            vols = (plan.get("outline", {}) or {}).get("volumes", []) or []
            inserted = False
            for vol in vols:
                chs = vol.get("chapters", []) or []
                for i, ch in enumerate(chs):
                    n = int(ch.get("number", 0))
                    if n >= next_num:
                        ch["number"] = n + 1
                        chs[i] = ch
                if not inserted and chapter_num < len(chs):
                    # 在 chapter_num 后插入新章占位
                    new_ch = dict(chs[chapter_num - 1]) if chapter_num - 1 < len(chs) else {}
                    new_ch["number"] = next_num
                    new_ch["title"] = f"第{next_num}章（拆分）"
                    new_ch["summary"] = ""
                    chs.insert(chapter_num, new_ch)
                    inserted = True
            from core.atomic_io import atomic_write_json
            atomic_write_json(os.path.join(novel_dir, "plan.json"), plan)
        except Exception as e:
            log.warning(f"Split plan renumber failed (non-fatal): {e}")
        return {"status": "ok", "op": "split", "chapter": chapter_num,
                "new_chapters": [chapter_num, next_num], "content": part1}

    # ── merge：两章合并 ──
    if op == "merge":
        if req.merge_with is None or req.merge_with == chapter_num:
            raise HTTPException(status_code=400, detail="merge 需要指定不同章节号（merge_with）")
        other = _read_ch(req.merge_with)
        if other is None:
            raise HTTPException(status_code=404, detail=f"第{req.merge_with}章不存在")
        lo, hi = sorted((chapter_num, req.merge_with))
        merged = _read_ch(lo) + "\n\n---\n\n" + _read_ch(hi)
        _write_ch(lo, merged)
        # 删除 hi 章文件并前移后续章节
        hi_path = os.path.join(chapters_dir, f"chapter_{hi:04d}.md")
        if os.path.exists(hi_path):
            os.remove(hi_path)
        existing = sorted(
            int(f[8:12]) for f in os.listdir(chapters_dir) if f.startswith("chapter_") and f.endswith(".md")
        )
        for fnum in sorted([e for e in existing if e > hi]):
            src = os.path.join(chapters_dir, f"chapter_{fnum:04d}.md")
            dst = os.path.join(chapters_dir, f"chapter_{fnum-1:04d}.md")
            if os.path.exists(src) and not os.path.exists(dst):
                os.rename(src, dst)
        # 更新 plan.json（删除 hi 章）
        try:
            plan = engine.get_novel(novel_id) or {}
            for vol in (plan.get("outline", {}) or {}).get("volumes", []) or []:
                chs = vol.get("chapters", []) or []
                chs[:] = [c for c in chs if int(c.get("number", 0)) != hi]
                for c in chs:
                    if int(c.get("number", 0)) > hi:
                        c["number"] = int(c["number"]) - 1
            from core.atomic_io import atomic_write_json
            atomic_write_json(os.path.join(novel_dir, "plan.json"), plan)
        except Exception as e:
            log.warning(f"Merge plan renumber failed (non-fatal): {e}")
        return {"status": "ok", "op": "merge", "chapter": lo, "content": merged}

    # ── AI 类操作（continue/expand/condense/rewrite）──
    sel_start, sel_end = 0, len(content)
    if req.selection and isinstance(req.selection, dict):
        sel_start = max(0, int(req.selection.get("start", 0)))
        sel_end = min(len(content), int(req.selection.get("end", len(content))))
        if sel_end <= sel_start:
            sel_start, sel_end = 0, len(content)

    selected = content[sel_start:sel_end]
    instruction = (req.instruction or "").strip()
    target_words = req.target_words or 1500

    if op == "continue":
        tail = content[-500:]
        prompt = (
            f"以下是小说第{chapter_num}章现有内容（结尾部分）：\n\n{tail}\n\n"
            f"请从断点处自然续写，保持相同的叙事视角、风格和节奏，"
            f"直到本章达到约 {target_words} 字。不要重复已写内容，不要解释，直接输出续写文本。"
        )
        result = await asyncio.to_thread(_ai_call, prompt, 0.8, 2500)
        new_content = content.rstrip() + "\n\n" + result.strip()
    elif op == "expand":
        prompt = (
            f"以下是小说某段文字：\n\n{selected}\n\n"
            f"请扩写这段：补充感官细节、动作层次、心理活动与环境描写，"
            f"不改变情节走向和人物关系，长度约为原文 2 倍。直接输出扩写后的文本。"
        )
        result = await asyncio.to_thread(_ai_call, prompt, 0.7, 2000)
        new_content = content[:sel_start] + result.strip() + content[sel_end:]
    elif op == "condense":
        prompt = (
            f"以下是小说某段文字：\n\n{selected}\n\n"
            f"请压缩这段至原文 40-60% 长度，保留所有关键信息、人物动作和情绪节点，"
            f"语言更精炼。直接输出压缩后的文本。"
        )
        result = await asyncio.to_thread(_ai_call, prompt, 0.5, 1500)
        new_content = content[:sel_start] + result.strip() + content[sel_end:]
    elif op == "rewrite":
        if not instruction:
            raise HTTPException(status_code=400, detail="rewrite 需要 instruction 修改意见")
        prompt = (
            f"以下是小说某段文字：\n\n{selected}\n\n"
            f"请按以下意见重写这段，保持叙事视角与风格一致：\n{instruction}\n"
            f"直接输出重写后的文本，不要解释。"
        )
        result = await asyncio.to_thread(_ai_call, prompt, 0.8, 2000)
        new_content = content[:sel_start] + result.strip() + content[sel_end:]
    else:
        raise HTTPException(status_code=400, detail=f"不支持的操作: {op}")

    _write_ch(chapter_num, new_content)
    return {"status": "ok", "op": op, "chapter": chapter_num, "content": new_content}


def _ai_call(prompt: str, temperature: float = 0.7, max_tokens: int = 1500) -> str:
    """统一 AI 调用（复用 writer._create 的重试与韧性客户端）。"""
    import asyncio
    try:
        resp = engine.writer._create(
            messages=[
                {"role": "system", "content": "你是资深网络小说作家，直接输出小说正文，不要任何解释、标题或 markdown 标记。"},
                {"role": "user", "content": prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI 调用失败: {e}")


# ── v2.8 创作者工具：章节版本历史 ──

@router.get("/api/novels/{novel_id}/chapters/{chapter_num}/versions")
async def chapter_versions(novel_id: str, chapter_num: int, request: Request):
    """获取章节版本历史（.versions/chapter_XXXX.vN.md）。"""
    _require_legacy_owner(request, novel_id)
    vdir = os.path.join(engine.memory.get_novel_dir(novel_id), ".versions")
    versions = []
    if os.path.isdir(vdir):
        for i in range(1, 12):
            fp = os.path.join(vdir, f"chapter_{chapter_num:04d}.v{i}.md")
            if os.path.exists(fp):
                with open(fp, "r", encoding="utf-8") as f:
                    versions.append({
                        "version": i, "chars": os.path.getsize(fp),
                        "preview": f.read()[:120],
                        "updated_at": os.path.getmtime(fp),
                    })
    versions.sort(key=lambda v: -v["version"])
    return {"chapter_num": chapter_num, "versions": versions}


@router.post("/api/novels/{novel_id}/chapters/{chapter_num}/versions/rollback")
async def rollback_chapter(novel_id: str, chapter_num: int, req: dict, request: Request):
    """回滚章节到指定版本（先快照当前版再覆盖）。"""
    _require_legacy_owner(request, novel_id)
    to_version = int((req or {}).get("to_version", 0))
    if to_version < 1:
        raise HTTPException(status_code=400, detail="to_version 必须 ≥ 1")
    vdir = os.path.join(engine.memory.get_novel_dir(novel_id), ".versions")
    src = os.path.join(vdir, f"chapter_{chapter_num:04d}.v{to_version}.md")
    if not os.path.exists(src):
        raise HTTPException(status_code=404, detail=f"版本 v{to_version} 不存在")
    with open(src, "r", encoding="utf-8") as f:
        content = f.read()
    # 先快照当前版（保留可再回滚）
    chapters_dir = os.path.join(engine.memory.get_novel_dir(novel_id), "chapters")
    fp = os.path.join(chapters_dir, f"chapter_{chapter_num:04d}.md")
    current = ""
    if os.path.exists(fp):
        with open(fp, "r", encoding="utf-8") as f:
            current = f.read()
    vdir2 = os.path.join(engine.memory.get_novel_dir(novel_id), ".versions")
    os.makedirs(vdir2, exist_ok=True)
    import shutil
    for i in range(4, 0, -1):
        s = os.path.join(vdir2, f"chapter_{chapter_num:04d}.v{i}.md")
        if os.path.exists(s):
            shutil.copy(s, os.path.join(vdir2, f"chapter_{chapter_num:04d}.v{i+1}.md"))
    with open(os.path.join(vdir2, f"chapter_{chapter_num:04d}.v1.md"), "w", encoding="utf-8") as f:
        f.write(current)
    with open(fp, "w", encoding="utf-8") as f:
        f.write(content)
    return {"status": "ok", "chapter": chapter_num, "restored_to": to_version, "content": content}


# ── v2.8 创作者工具：写作统计仪表盘 ──

@router.get("/api/novels/{novel_id}/stats")
async def novel_stats(novel_id: str, request: Request):
    """聚合写作统计：总字数/章节/均值/每日趋势/每章分布/目标。"""
    _require_legacy_owner(request, novel_id)
    novel_dir = engine.memory.get_novel_dir(novel_id)
    chapters_dir = os.path.join(novel_dir, "chapters")

    per_chapter = []
    total_chars = 0
    daily = {}
    if os.path.isdir(chapters_dir):
        for fname in sorted(os.listdir(chapters_dir)):
            if not fname.endswith(".md"):
                continue
            try:
                ch_num = int("".join(c for c in fname if c.isdigit()) or "0")
                fpath = os.path.join(chapters_dir, fname)
                with open(fpath, "r", encoding="utf-8") as f:
                    text = f.read()
                n = len(text)
                total_chars += n
                import time as _t
                mtime = os.path.getmtime(fpath)
                day = _t.strftime("%Y-%m-%d", _t.localtime(mtime))
                daily[day] = daily.get(day, 0) + n
                per_chapter.append({"num": ch_num, "chars": n})
            except Exception:
                continue

    per_chapter.sort(key=lambda x: x["num"])
    state = engine.memory.get_novel_state(novel_id)
    completed = state.get("completed_chapters", []) if isinstance(state, dict) else []
    plan = engine.get_novel(novel_id) or {}
    total_planned = int((plan.get("outline", {}) or {}).get("total_chapters", 0) or 0)
    goals = (plan.get("_meta", {}) or {}).get("goals", {}) if isinstance(plan, dict) else {}

    return {
        "total_chars": total_chars,
        "total_chapters": len(per_chapter),
        "planned_chapters": total_planned,
        "avg_chars_per_chapter": round(total_chars / len(per_chapter)) if per_chapter else 0,
        "completed_chapters": completed,
        "daily": [{"date": d, "chars": c} for d, c in sorted(daily.items())],
        "per_chapter": per_chapter,
        "goal": {
            "target_chars": int(goals.get("target_chars", 0) or 0),
            "target_chapters": int(goals.get("target_chapters", total_planned) or 0),
            "daily_target": int(goals.get("daily_target", 0) or 0),
        },
    }


@router.put("/api/novels/{novel_id}/stats/goal")
async def save_goal(novel_id: str, req: dict, request: Request):
    """保存写作目标到 plan._meta.goals。"""
    _require_legacy_owner(request, novel_id)
    plan = engine.get_novel(novel_id) or {}
    plan.setdefault("_meta", {})["goals"] = {
        "target_chars": int((req or {}).get("target_chars", 0) or 0),
        "target_chapters": int((req or {}).get("target_chapters", 0) or 0),
        "daily_target": int((req or {}).get("daily_target", 0) or 0),
    }
    novel_dir = engine.memory.get_novel_dir(novel_id)
    atomic_write_json(os.path.join(novel_dir, "plan.json"), plan)
    return {"status": "ok", "goals": plan["_meta"]["goals"]}


# ── v2.8 创作者工具：设定一致性扫描 ──

@router.post("/api/novels/{novel_id}/scan-consistency")
async def scan_consistency(novel_id: str, request: Request):
    """跨章设定一致性扫描（实体属性矛盾检测，同步执行 10-30s）。"""
    _require_legacy_owner(request, novel_id)
    try:
        from core.settings_scanner import SettingsScanner
        scanner = SettingsScanner(engine.client, engine.model)
        # v2.8.5: scan 内含同步 LLM（10-30s），放线程池避免阻塞事件循环
        result = await asyncio.to_thread(scanner.scan, novel_id, engine.get_novel(novel_id) or {})
        return result
    except ImportError:
        raise HTTPException(status_code=501, detail="settings_scanner 模块不可用")
    except Exception as e:
        log.warning(f"Consistency scan failed: {e}")
        raise HTTPException(status_code=500, detail=f"扫描失败: {e}")
