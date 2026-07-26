"""NovelGenerator — FastAPI Server (serves frontend + API)"""
import json
import asyncio
import logging
import sys
import os
import urllib.parse

# 提高递归深度限制，防止大型 JSON 解析时触发 RecursionError
sys.setrecursionlimit(10000)

# Load .env from project root (local dev only; Render uses env vars)
try:
    from dotenv import load_dotenv
    _ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _dotenv_path = os.path.join(_ROOT, ".env")
    if os.path.exists(_dotenv_path):
        load_dotenv(_dotenv_path)
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, PlainTextResponse, FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional

from core.engine import NovelEngine
from core.pacing_checker import PacingChecker
from core.style_fingerprint import StyleFingerprint
from config import CORS_ORIGINS, HOST, PORT, NOVELS_DIR, DEFAULT_CHAPTER_WORDS

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
log = logging.getLogger("api")

app = FastAPI(title="NovelGenerator API", version="0.9.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = NovelEngine()

# 前端文件目录 — 使用 abspath 防止 __file__ 为相对路径时解析错误
# PC: __file__ = .../backend/api/server.py, web/ 在 .../web/ (3层)
# Android: __file__ = .../api/server.py, web/ 在 .../web/ (2层)
_api_dir = os.path.dirname(os.path.abspath(__file__))
_python_root = os.path.dirname(_api_dir)
_pc_root = os.path.dirname(_python_root)
_web_dir_2 = os.path.join(_python_root, "web")
_web_dir_3 = os.path.join(_pc_root, "web")
if os.path.isdir(_web_dir_2):
    WEB_DIR = _web_dir_2
elif os.path.isdir(_web_dir_3):
    WEB_DIR = _web_dir_3
else:
    WEB_DIR = os.getenv("WEB_DIR", _web_dir_2)


# ── Frontend Route (仅/，子路径走 StaticFiles) ──

@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    """Serve the SPA frontend"""
    index_path = os.path.join(WEB_DIR, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return f.read()
    return HTMLResponse("<h1>NovelGenerator</h1><p>Frontend not found</p>", status_code=404)


# ── Static JS/CSS assets (自托管，不依赖外部CDN) ──

@app.get("/vue.global.prod.js")
async def serve_vue():
    path = os.path.join(WEB_DIR, "vue.global.prod.js")
    if os.path.exists(path):
        return FileResponse(path, media_type="application/javascript")
    return Response("// vue not found", status_code=404)

@app.get("/sw.js")
async def serve_sw():
    path = os.path.join(WEB_DIR, "sw.js")
    if os.path.exists(path):
        return FileResponse(path, media_type="application/javascript")
    return Response("// sw.js not found", media_type="application/javascript", status_code=404)

@app.get("/manifest.json")
async def serve_manifest():
    path = os.path.join(WEB_DIR, "manifest.json")
    if os.path.exists(path):
        return FileResponse(path, media_type="application/json")
    return {}


# ── API Routes ──

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


# ── Routes ──

@app.get("/api/health")
async def health():
    import os as _os
    novels_exist = _os.path.exists(NOVELS_DIR)
    novel_count = len([f for f in _os.listdir(NOVELS_DIR) if _os.path.isdir(_os.path.join(NOVELS_DIR, f)) and f != ".gitkeep"]) if novels_exist else 0
    # 列出所有 novel 目录和对应的书名
    novel_list = []
    if novels_exist:
        for d in sorted(_os.listdir(NOVELS_DIR)):
            dpath = _os.path.join(NOVELS_DIR, d)
            if _os.path.isdir(dpath) and d != ".gitkeep":
                plan_f = _os.path.join(dpath, "plan.json")
                title = d
                if _os.path.exists(plan_f):
                    try:
                        import json
                        with open(plan_f, 'r', encoding='utf-8') as f:
                            pd = json.load(f)
                        title = pd.get("title", d) if isinstance(pd, dict) else d
                    except: pass
                novel_list.append({"dir": d, "title": title, "has_plan": _os.path.exists(plan_f)})
    return {
        "status": "ok",
        "service": "NovelGenerator",
        "storage": NOVELS_DIR,
        "storage_exists": novels_exist,
        "novel_count": novel_count,
        "novels": novel_list,
    }


@app.get("/api/styles")
async def get_styles():
    """返回所有可用风格（分组）"""
    from core.styles import get_style_categories, STYLES
    categories = get_style_categories()
    result = {}
    for cat, names in categories.items():
        items = []
        for n in names:
            s = STYLES.get(n)
            if not s: continue
            items.append({
                "name": n,
                "author": s["author"],
                "desc": s.get("prose", "")[:80] + "…",
                "is_custom": s.get("is_custom", False),
            })
        if items:
            result[cat] = items
    return {"categories": result}


@app.get("/api/styles/params")
async def get_style_params():
    """返回自定义风格的参数化配置选项"""
    from core.styles import CUSTOM_STYLE_PARAMS
    return {"params": CUSTOM_STYLE_PARAMS}


@app.post("/api/styles/build-custom")
async def build_custom_style_api(req: dict):
    """从用户选择的参数构建自定义风格"""
    from core.styles import build_parameterized_style
    style = build_parameterized_style(req)
    return {"style": style}


# ── Style Seeds ──

import shutil
STYLE_SEEDS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "style_seeds")
os.makedirs(STYLE_SEEDS_DIR, exist_ok=True)


@app.get("/api/styles/seeds")
async def list_style_seeds():
    """列出所有保存的风格种子"""
    seeds = []
    if os.path.exists(STYLE_SEEDS_DIR):
        for fname in os.listdir(STYLE_SEEDS_DIR):
            if fname.endswith(".json"):
                path = os.path.join(STYLE_SEEDS_DIR, fname)
                with open(path, "r", encoding="utf-8") as f:
                    seed = json.load(f)
                    seeds.append({"name": seed.get("name", fname[:-5]), "author": seed.get("author", ""), "filename": fname})
    return {"seeds": seeds}


@app.post("/api/styles/seeds")
async def save_style_seed(seed: dict):
    """保存风格种子"""
    name = seed.get("name", "未命名").strip()
    if not name:
        raise HTTPException(status_code=400, detail="风格名称不能为空")
    safe_name = "".join(c for c in name if c.isalnum() or c in " _-") or "custom_style"
    path = os.path.join(STYLE_SEEDS_DIR, f"{safe_name}.json")
    seed["saved_at"] = __import__("datetime").datetime.now().isoformat()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(seed, f, ensure_ascii=False, indent=2)
    return {"success": True, "name": safe_name}


@app.delete("/api/styles/seeds/{name}")
async def delete_style_seed(name: str):
    path = os.path.join(STYLE_SEEDS_DIR, f"{name}.json")
    if os.path.exists(path):
        os.remove(path)
    return {"success": True}


@app.get("/api/novels")
async def list_novels():
    """列出所有小说"""
    return {"novels": engine.list_novels()}


@app.get("/api/novels/{novel_id}")
async def get_novel(novel_id: str):
    """获取小说详情"""
    plan = engine.get_novel(novel_id)
    if not plan:
        raise HTTPException(status_code=404, detail=f"小说 '{novel_id}' 不存在")
    
    # 移除过大的章节内容
    if "chapters" in plan:
        del plan["chapters"]
    
    return {"novel": plan}


@app.get("/api/novels/{novel_id}/chapters/{chapter_num}")
async def get_chapter(novel_id: str, chapter_num: int):
    """读取单章正文"""
    content = engine.get_chapter(novel_id, chapter_num)
    if content is None:
        raise HTTPException(status_code=404, detail=f"第{chapter_num}章不存在")
    return {"content": content, "chapter_num": chapter_num}


@app.get("/api/novels/{novel_id}/chapters/{chapter_num}/exists")
async def chapter_exists(novel_id: str, chapter_num: int):
    """检查章节文件是否存在（避免前端切换空白）"""
    exists = engine.memory.chapter_exists(novel_id, chapter_num)
    return {"exists": exists, "chapter_num": chapter_num}


@app.post("/api/novels/{novel_id}/sync-state")
async def sync_novel_state(novel_id: str):
    """修复 state.json 与实际文件不同步的问题"""
    state = engine.memory.get_novel_state(novel_id)
    chapters = engine.memory.scan_chapters(novel_id)
    return {
        "state": state,
        "chapters_on_disk": chapters,
        "synced": state.get("completed_chapters") == chapters,
    }


@app.put("/api/novels/{novel_id}")
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


@app.post("/api/novels")
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


@app.post("/api/novels/create-stream")
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


async def _sse_with_heartbeat(event_generator):
    """通用心跳包装: 每8s发送ping防止Render超时断开SSE"""
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


@app.post("/api/novels/generate")
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


@app.post("/api/novels/generate/atomic")
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


@app.post("/api/novels/{novel_id}/generate/batch")
async def generate_batch(novel_id: str, req: dict):
    """批量生成章节 (SSE 流式进度)"""
    start = req.get("start_chapter", 1)
    end = req.get("end_chapter", 1)
    writing_mode = req.get("writing_mode", "webnovel")
    
    async def event_stream():
        try:
            failed = []
            for ch_num in range(start, end + 1):
                yield f"data: {json.dumps({'type':'progress','chapter':ch_num,'total':end,'start':start}, ensure_ascii=False)}\n\n"
                chapter_error = None
                try:
                    async for event in engine.generate_chapter_stream(
                        novel_id, ch_num, writing_mode
                    ):
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                        if event.get("type") == "error":
                            chapter_error = event.get("message", "未知错误")
                    if not chapter_error:
                        yield f"data: {json.dumps({'type':'chapter_done','chapter':ch_num}, ensure_ascii=False)}\n\n"
                except Exception as ch_err:
                    chapter_error = str(ch_err)
                    log.warning(f"Batch chapter {ch_num} exception: {ch_err}")
                
                if chapter_error:
                    failed.append(ch_num)
                    yield f"data: {json.dumps({'type':'chapter_failed','chapter':ch_num,'error':chapter_error}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type':'batch_done','from':start,'to':end,'failed':failed}, ensure_ascii=False)}\n\n"
        except Exception as e:
            log.exception("batch generate crashed")
            yield f"data: {json.dumps({'type':'error','message':str(e)}, ensure_ascii=False)}\n\n"
    
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@app.get("/api/novels/{novel_id}/export")
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


async def export_novel_pdf(novel_id: str):
    """导出为 PDF"""
    try:
        from fpdf import FPDF
    except ImportError:
        raise HTTPException(status_code=500, detail="PDF 导出需要安装 fpdf2: pip install fpdf2")
    
    content, err = engine.export_novel(novel_id, "txt")
    if err:
        raise HTTPException(status_code=404, detail=err)
    
    plan = engine.get_novel(novel_id)
    title = plan.get("title", novel_id) if plan else novel_id
    
    pdf = FPDF()
    pdf.add_page()
    
    # 添加中文字体
    font_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "fonts")
    os.makedirs(font_dir, exist_ok=True)
    
    # 尝试使用系统字体或内置字体
    font_used = False
    for font_name in ["simsun.ttc", "simsun.ttf", "msyh.ttc", "msyh.ttf", "NotoSansSC-Regular.ttf"]:
        font_path = os.path.join(font_dir, font_name)
        if os.path.exists(font_path):
            pdf.add_font("CJK", "", font_path, uni=True)
            pdf.set_font("CJK", "", 12)
            font_used = True
            break
    
    if not font_used:
        # fallback: 无中文字体时用内置字体（中文会显示为方块，但英文正常）
        pdf.set_font("Helvetica", "", 12)
    
    # 书名页
    pdf.set_font("CJK", "", 18) if font_used else pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 20, title, new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(10)
    
    if plan:
        pdf.set_font("CJK", "", 10) if font_used else pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 8, f"题材: {plan.get('genre','')}  风格: {plan.get('style','')}", new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.ln(10)
    
    # 正文
    pdf.set_font("CJK", "", 11) if font_used else pdf.set_font("Helvetica", "", 11)
    
    for line in content.split("\n"):
        line = line.strip()
        if not line:
            pdf.ln(4)
            continue
        
        if line.startswith("# "):
            pdf.set_font("CJK", "", 14) if font_used else pdf.set_font("Helvetica", "B", 14)
            pdf.cell(0, 10, line.lstrip("# "), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(4)
            pdf.set_font("CJK", "", 11) if font_used else pdf.set_font("Helvetica", "", 11)
        elif line.startswith("-" * 10):
            pdf.ln(6)
        else:
            # 中文按字符宽度自动换行
            pdf.multi_cell(0, 6, line)
            pdf.ln(2)
    
    pdf_bytes = pdf.output()
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={novel_id}.pdf"}
    )

from fastapi.responses import Response


async def export_novel_epub(novel_id: str):
    """导出为 EPUB"""
    content, err = engine.export_novel(novel_id, "epub")
    if err:
        raise HTTPException(status_code=404, detail=err)
    
    return Response(
        content=content,
        media_type="application/epub+zip",
        headers={"Content-Disposition": f"attachment; filename={novel_id}.epub"}
    )


@app.post("/api/novels/export/batch")
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


@app.post("/api/novels/{novel_id}/regenerate-outline")
async def regenerate_outline(novel_id: str, req: dict):
    """根据修改意见重新生成大纲（保留世界观和角色）+ 心跳"""
    feedback = req.get("feedback", "")
    if not feedback.strip():
        raise HTTPException(status_code=400, detail="请输入修改意见")
    
    async def event_stream():
        async for data in _sse_with_heartbeat(
            engine.regenerate_outline_stream(novel_id, feedback)
        ):
            yield data
    
    return StreamingResponse(event_stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/novels/{novel_id}/interactive-outline")
async def interactive_outline(novel_id: str, req: dict):
    """v2 交互式大纲: FeedbackDecomposer 语义拆解 → 逐条精确执行 → diff输出 + 心跳"""
    feedback = req.get("feedback", "")
    if not feedback.strip():
        raise HTTPException(status_code=400, detail="请输入修改意见")
    
    async def event_stream():
        async for data in _sse_with_heartbeat(
            engine.interactive_outline_stream(novel_id, feedback)
        ):
            yield data
    
    return StreamingResponse(event_stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/novels/{novel_id}/decompose-feedback")
async def decompose_feedback(novel_id: str, req: dict):
    """仅拆解反馈为修改计划（不执行），供前端预览"""
    feedback = req.get("feedback", "")
    if not feedback.strip():
        raise HTTPException(status_code=400, detail="请输入修改意见")
    result = engine.decompose_feedback(novel_id, feedback)
    return {"result": result}


# ── Chapter Feedback (章节修改意见) ──

@app.post("/api/novels/{novel_id}/chapter-feedback/{chapter_num}")
async def chapter_feedback(novel_id: str, chapter_num: int, req: dict):
    """对已生成章节提出修改意见 — 拆解为具体指令"""
    feedback = req.get("feedback", "")
    if not feedback.strip():
        raise HTTPException(status_code=400, detail="请输入修改意见")
    
    plan = engine.get_novel(novel_id)
    if not plan:
        raise HTTPException(status_code=404, detail=f"小说 '{novel_id}' 不存在")
    
    chapter_outline = engine._find_chapter_outline(plan, chapter_num)
    if not chapter_outline:
        raise HTTPException(status_code=404, detail=f"第{chapter_num}章大纲不存在")
    
    result = engine.feedback_decomposer.decompose_for_chapter(
        feedback, chapter_num, chapter_outline, plan
    )
    return {"result": result}


# ── Logic Supervisor (v2.3 全维度) ──

@app.post("/api/novels/{novel_id}/logic-check/{chapter_num}")
async def logic_check_chapter(novel_id: str, chapter_num: int, req: dict = None):
    """全维度逻辑监督 — 12 大类逻辑错误检测 + 分类得分 + 修复提示"""
    run_deep = req.get("run_deep", True) if req else True
    result = engine.validate_chapter_consistency(novel_id, chapter_num, run_deep=run_deep)
    # 附加修复提示
    if result.get("violations") or result.get("warnings"):
        result["fix_prompt"] = engine.build_logic_fix_prompt(result)
    return {"result": result}


@app.post("/api/novels/{novel_id}/logic-check-batch")
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


# ── Consistency Validator (原有接口，保持兼容) ──

@app.post("/api/novels/{novel_id}/validate-chapter/{chapter_num}")
async def validate_chapter_consistency(novel_id: str, chapter_num: int, req: dict = None):
    """对已生成章节执行逻辑一致性校验"""
    run_deep = req.get("run_deep", True) if req else True
    result = engine.validate_chapter_consistency(novel_id, chapter_num, run_deep=run_deep)
    return {"result": result}


@app.get("/api/novels/{novel_id}/validate-outline")
async def validate_outline_consistency(novel_id: str):
    """校验大纲逻辑一致性"""
    result = engine.validate_outline_consistency(novel_id)
    return {"result": result}


# ── Opening Optimizer ──

@app.post("/api/novels/{novel_id}/analyze-opening")
async def analyze_opening(novel_id: str, req: dict = None):
    """分析章节开头吸引力"""
    chapter_num = req.get("chapter_num", 1) if req else 1
    result = engine.analyze_opening(novel_id, chapter_num)
    return {"result": result}


@app.post("/api/novels/{novel_id}/opening-alternatives")
async def opening_alternatives(novel_id: str, req: dict):
    """生成替代开头方案"""
    chapter_num = req.get("chapter_num", 1)
    count = req.get("count", 3)
    result = await engine.generate_opening_alternatives(novel_id, chapter_num, count)
    return {"alternatives": result}


# ── Twist Designer ──

@app.get("/api/novels/{novel_id}/design-twists")
async def design_twists(novel_id: str):
    """为整部小说规划反转点"""
    result = engine.design_twists(novel_id)
    return {"result": result}


# ── Chapter Summarizer ──

@app.post("/api/novels/{novel_id}/summarize")
async def summarize_chapters(novel_id: str, req: dict):
    """触发渐进式摘要压缩"""
    chapter_num = req.get("chapter_num", 0)
    result = check_and_compress(engine.memory, novel_id, chapter_num, engine.chapter_summarizer)
    return {"result": result}


@app.get("/api/novels/{novel_id}/token-budget")
async def get_token_budget(novel_id: str):
    """查看当前小说的 token 预算"""
    state = engine.memory.get_novel_state(novel_id)
    total = state.get("total_chapters", 0)
    current = state.get("current_chapter", 0)
    budget = engine.chapter_summarizer.get_token_budget(current)
    return {"total_chapters": total, "current_chapter": current, "budget": budget}


@app.post("/api/novels/{novel_id}/design-chapter-twist")
async def design_chapter_twist(novel_id: str, req: dict):
    """为单章设计反转钩子"""
    chapter_num = req.get("chapter_num", 1)
    result = engine.design_chapter_twist(novel_id, chapter_num)
    return {"result": result}


# ── Pacing Check ──

@app.post("/api/novels/{novel_id}/pacing-check/{chapter_num}")
async def check_pacing(novel_id: str, chapter_num: int):
    """检查单章节奏质量"""
    content = engine.get_chapter(novel_id, chapter_num)
    if content is None:
        raise HTTPException(status_code=404, detail=f"第{chapter_num}章不存在")
    
    result = engine.pacing_checker.analyze(content, chapter_num)
    return {"result": result}


# ── Style Fingerprint ──

fingerprinter = StyleFingerprint()

@app.post("/api/styles/fingerprint")
async def style_fingerprint(req: dict):
    """分析文本的风格指纹（5维DNA）"""
    text = req.get("text", "")
    if not text or len(text) < 500:
        raise HTTPException(status_code=400, detail="至少需要500字")
    
    fp = fingerprinter.analyze(text)
    return {"fingerprint": fp}


@app.post("/api/styles/compare")
async def compare_styles(req: dict):
    """对比两个文本的风格差异"""
    text_a = req.get("text_a", "")
    text_b = req.get("text_b", "")
    if not text_a or not text_b:
        raise HTTPException(status_code=400, detail="需要两个文本")
    
    comparison = fingerprinter.compare(text_a, text_b)
    return {"comparison": comparison}


@app.get("/api/novels/{novel_id}/character-bible")
async def get_character_bible(novel_id: str):
    """获取人物宝典"""
    bible_path = os.path.join(engine.memory.get_novel_dir(novel_id), "character_bible.json")
    if not os.path.exists(bible_path):
        raise HTTPException(status_code=404, detail="人物宝典尚未生成")
    
    with open(bible_path, "r", encoding="utf-8") as f:
        bible = json.load(f)
    return {"bible": bible}


# ── Main ──



# ── 多Agent需求拆解与监督 ──

@app.post("/api/requirements/preview-decompose")
async def preview_decompose(req: dict):
    """预拆解用户灵感（创建小说前预览需求拆解结果）
    
    v2.2: 新增端点，让用户在创建小说之前预览AI对其需求的理解。
    不依赖 novel_id，直接拆解灵感文本。
    """
    inspiration = req.get("inspiration", "")
    if not inspiration.strip():
        raise HTTPException(status_code=400, detail="请输入灵感或需求")
    
    # 直接拆解，不关联任何小说
    result = engine.requirement_decomposer.decompose(inspiration)
    
    # 同时返回阶段上下文
    phase_context = engine.requirement_decomposer.decompose_to_context(result)
    
    return {
        "requirements": result,
        "phase_context": {
            "worldbuilding_count": len(phase_context.get("worldbuilding_context", "").split("---")) if phase_context.get("worldbuilding_context") else 0,
            "character_count": len(phase_context.get("character_context", "").split("---")) if phase_context.get("character_context") else 0,
            "outline_count": len(phase_context.get("outline_context", "").split("---")) if phase_context.get("outline_context") else 0,
            "p0_count": len(phase_context.get("p0_requirements", [])),
        }
    }


@app.post("/api/novels/{novel_id}/requirements/decompose")
async def decompose_requirements(novel_id: str, req: dict):
    """拆解用户灵感为可执行子任务（关联已创建的小说）"""
    inspiration = req.get("inspiration", "")
    if not inspiration.strip():
        raise HTTPException(status_code=400, detail="请输入灵感或需求")
    result = engine.decompose_requirements(novel_id, inspiration)
    return {"requirements": result}


@app.post("/api/novels/{novel_id}/requirements/update")
async def update_requirements(novel_id: str, req: dict):
    """追加/修改需求"""
    inspiration = req.get("inspiration", "")
    if not inspiration.strip():
        raise HTTPException(status_code=400, detail="请输入追加的需求")
    result = engine.update_requirements(novel_id, inspiration)
    return {"requirements": result}


@app.get("/api/novels/{novel_id}/requirements")
async def get_requirements(novel_id: str):
    """获取当前需求列表"""
    reqs = engine._requirements.get(novel_id, {})
    if not reqs:
        raise HTTPException(status_code=404, detail="尚未拆解需求")
    return reqs


@app.post("/api/novels/{novel_id}/requirements/supervise")
async def supervise_requirements(novel_id: str):
    """监督当前方案是否满足需求"""
    result = engine.supervise_requirements(novel_id)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.post("/api/novels/{novel_id}/requirements/verify-loop")
async def verify_and_fix_loop(novel_id: str):
    """循环校验：监督→修正→再监督"""
    async def event_stream():
        async for data in _sse_with_heartbeat(
            engine.verify_and_fix_loop(novel_id)
        ):
            yield data

    return StreamingResponse(event_stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── v2.3: 剧情图谱 & 弧规划 & 校准 API ──

def _read_novel_file(novel_id: str, filename: str) -> dict:
    """安全读取小说目录下的 JSON 文件"""
    novel_dir = engine.memory.get_novel_dir(novel_id)
    path = os.path.join(novel_dir, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"{filename} 不存在，请先生成大纲")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取 {filename} 失败: {str(e)}")


@app.get("/api/novels/{novel_id}/storygraph")
async def get_storygraph(novel_id: str, chapter: int = 0):
    """获取剧情图谱数据（剧情线/伏笔账本/角色快照/因果链）
    
    Query params:
        chapter: 指定章节号，仅返回该章及之前的剧情数据（时间回溯）
                 0或省略 = 返回全部
    """
    data = _read_novel_file(novel_id, "storygraph.json")
    last_updated = data.get("last_updated_chapter", 0)
    version = data.get("version", 0)
    
    # 如果指定了章节号，过滤到该时间点
    if chapter > 0 and chapter < last_updated:
        data = _filter_storygraph_to_chapter(data, chapter)
    
    return {
        "novel_id": novel_id,
        "plot_threads": data.get("plot_threads", {}),
        "foreshadow_ledger": data.get("foreshadow_ledger", {}),
        "char_snapshots": data.get("char_snapshots", {}),
        "causal_links": data.get("causal_links", []),
        "version": version,
        "last_updated_chapter": last_updated,
        "filtered_to_chapter": chapter if chapter > 0 and chapter < last_updated else None,
        # 计算摘要统计
        "stats": _compute_storygraph_stats(data),
    }


def _filter_storygraph_to_chapter(data: dict, chapter: int) -> dict:
    """将剧情图谱数据过滤到指定章节时间点"""
    import copy
    filtered = copy.deepcopy(data)
    
    # 过滤剧情线节点（只保留 chapter <= 指定章的）
    for tid in filtered.get("plot_threads", {}):
        t = filtered["plot_threads"][tid]
        t["key_nodes"] = [n for n in t.get("key_nodes", []) if n["chapter"] <= chapter]
        # 如果在指定章时该线程还没有节点，状态回退
        if not t["key_nodes"] and t.get("status") in ("advancing", "climax", "resolved"):
            t["status"] = "active"
    
    # 过滤伏笔（只保留 planted_chapter <= 指定章的）
    filtered["foreshadow_ledger"] = {
        fid: fs for fid, fs in filtered.get("foreshadow_ledger", {}).items()
        if fs.get("planted_chapter", 0) <= chapter
    }
    # 回退伏笔状态：在指定章时尚未回收的，状态恢复为hinted/planted
    for fs in filtered["foreshadow_ledger"].values():
        if fs.get("actual_payoff_chapter") and fs["actual_payoff_chapter"] > chapter:
            fs["actual_payoff_chapter"] = None
            fs["status"] = "hinted" if fs.get("hint_count", 1) > 1 else "planted"
        if fs.get("last_hint_chapter", 0) > chapter:
            fs["last_hint_chapter"] = max(fs.get("planted_chapter", 0), 
                                          min(n for n in [fs.get("planted_chapter",0)] if n <= chapter))
    
    # 过滤角色快照（回退到指定章时的状态）
    for name in filtered.get("char_snapshots", {}):
        snap = filtered["char_snapshots"][name]
        if snap.get("last_chapter_appeared", 0) > chapter:
            snap["last_chapter_appeared"] = 0
        # 过滤关系变化
        snap["relationship_changes"] = [
            rc for rc in snap.get("relationship_changes", [])
            if rc.get("chapter", 0) <= chapter
        ]
    
    # 过滤因果链
    filtered["causal_links"] = [
        cl for cl in filtered.get("causal_links", [])
        if cl.get("cause_chapter", 0) <= chapter
    ]
    
    filtered["last_updated_chapter"] = chapter
    filtered["filtered"] = True
    return filtered


def _compute_storygraph_stats(data: dict) -> dict:
    """计算剧情图谱统计摘要"""
    return {
        "total_threads": len(data.get("plot_threads", {})),
        "active_threads": sum(1 for t in data.get("plot_threads", {}).values() if t.get("status") in ("active", "advancing", "climax")),
        "total_foreshadows": len(data.get("foreshadow_ledger", {})),
        "unresolved_foreshadows": sum(1 for f in data.get("foreshadow_ledger", {}).values() if f.get("status") in ("planted", "hinted")),
        "resolved_foreshadows": sum(1 for f in data.get("foreshadow_ledger", {}).values() if f.get("status") == "resolved"),
        "tracked_characters": len(data.get("char_snapshots", {})),
        "causal_links": len(data.get("causal_links", [])),
    }


@app.get("/api/novels/{novel_id}/arcs")
async def get_arcs(novel_id: str):
    """获取剧情弧规划数据"""
    data = _read_novel_file(novel_id, "arcplans.json")
    arcs = data.get("arcs", [])
    # 计算当前弧
    state = engine.memory.get_novel_state(novel_id)
    current_chapter = state.get("completed_chapters", 0) + 1
    current_arc = None
    for arc in arcs:
        ch_list = arc.get("chapters", [])
        if ch_list and current_chapter in ch_list:
            pos = ch_list.index(current_chapter) + 1
            current_arc = {**arc, "current_position": pos, "total_in_arc": len(ch_list)}
            break
    return {
        "novel_id": novel_id,
        "arcs": arcs,
        "current_chapter": current_chapter,
        "current_arc": current_arc,
        "stats": {
            "total_arcs": len(arcs),
            "completed_arcs": sum(1 for a in arcs if a.get("end_chapter", 0) < current_chapter),
            "type_distribution": {
                t: sum(1 for a in arcs if a.get("type") == t)
                for t in ["setup", "rising", "climax", "resolution"]
            },
        }
    }


@app.get("/api/novels/{novel_id}/calibration")
async def get_calibration(novel_id: str):
    """获取最新的剧情校准报告"""
    data = _read_novel_file(novel_id, "calibration.json")
    return data


# ── v2.4: 剧情图谱可视化数据 ──

@app.get("/api/novels/{novel_id}/storygraph/visualization")
async def get_storygraph_visualization(novel_id: str, chapter: int = 0):
    """获取剧情图谱可视化数据：人物关系图 + 剧情线图
    
    Query params:
        chapter: 指定章节号过滤（0=全部）
    """
    data = _read_novel_file(novel_id, "storygraph.json")
    if chapter > 0 and chapter < data.get("last_updated_chapter", 0):
        data = _filter_storygraph_to_chapter(data, chapter)
    
    return {
        "novel_id": novel_id,
        "character_relations": _build_character_relation_graph(data),
        "plot_timeline": _build_plot_timeline(data),
    }


# ── v2.5: 剧情图谱编辑器 API ──

def _write_novel_file(novel_id: str, filename: str, data: dict):
    """安全写入小说目录下的 JSON 文件（原子写入）"""
    from core.atomic_io import atomic_write_json
    novel_dir = engine.memory.get_novel_dir(novel_id)
    path = os.path.join(novel_dir, filename)
    atomic_write_json(path, data)


def _validate_thread_fields(body: dict, is_new: bool = False):
    """校验剧情线字段"""
    ALLOWED_TYPES = {"main_plot", "subplot", "character_arc", "mystery"}
    ALLOWED_STATUS = {"dormant", "active", "advancing", "climax", "resolved"}
    
    if is_new and not body.get("name"):
        raise HTTPException(400, "name 为必填字段")
    if "type" in body and body["type"] not in ALLOWED_TYPES:
        raise HTTPException(400, f"type 必须是 {ALLOWED_TYPES} 之一")
    if "status" in body and body["status"] not in ALLOWED_STATUS:
        raise HTTPException(400, f"status 必须是 {ALLOWED_STATUS} 之一")
    if "priority" in body and not (1 <= body["priority"] <= 5):
        raise HTTPException(400, "priority 必须在 1-5 之间")
    if "current_tension" in body and not (1 <= body["current_tension"] <= 10):
        raise HTTPException(400, "current_tension 必须在 1-10 之间")
    if "name" in body and len(body["name"]) > 30:
        raise HTTPException(400, "name 最长 30 字")
    if "description" in body and len(body["description"]) > 200:
        raise HTTPException(400, "description 最长 200 字")


# ── 剧情线编辑 ──

@app.put("/api/novels/{novel_id}/storygraph/threads/{thread_id}")
async def update_thread(novel_id: str, thread_id: str, body: dict):
    """更新或创建剧情线（partial update）"""
    _validate_thread_fields(body)
    
    data = _read_novel_file(novel_id, "storygraph.json")
    threads = data.setdefault("plot_threads", {})
    
    if thread_id not in threads:
        threads[thread_id] = {
            "id": thread_id, "name": body.get("name", thread_id),
            "type": "subplot", "status": "active", "priority": 3,
            "description": "", "key_nodes": [], "next_planned": "",
            "current_tension": 5, "characters": [],
        }
    
    thread = threads[thread_id]
    updatable = ("name", "type", "status", "priority", "description",
                 "current_tension", "next_planned", "characters", "key_nodes")
    for key in updatable:
        if key in body:
            thread[key] = body[key]
    
    data["version"] = data.get("version", 0) + 1
    _write_novel_file(novel_id, "storygraph.json", data)
    engine.memory.invalidate_all(novel_id)
    return {"ok": True, "thread_id": thread_id}


@app.delete("/api/novels/{novel_id}/storygraph/threads/{thread_id}")
async def delete_thread(novel_id: str, thread_id: str):
    """软删除剧情线（标记为 resolved）"""
    data = _read_novel_file(novel_id, "storygraph.json")
    if thread_id not in data.get("plot_threads", {}):
        raise HTTPException(404, "剧情线不存在")
    data["plot_threads"][thread_id]["status"] = "resolved"
    data["version"] = data.get("version", 0) + 1
    _write_novel_file(novel_id, "storygraph.json", data)
    engine.memory.invalidate_all(novel_id)
    return {"ok": True}


# ── 伏笔编辑 ──

@app.put("/api/novels/{novel_id}/storygraph/foreshadows/{fs_id}")
async def update_foreshadow(novel_id: str, fs_id: str, body: dict):
    """更新伏笔"""
    data = _read_novel_file(novel_id, "storygraph.json")
    ledger = data.setdefault("foreshadow_ledger", {})
    
    if fs_id not in ledger:
        raise HTTPException(404, "伏笔不存在")
    
    fs = ledger[fs_id]
    updatable = ("description", "planned_payoff_chapter", "status",
                 "importance", "thread_id")
    for key in updatable:
        if key in body:
            fs[key] = body[key]
    
    # 如果手动标记为 resolved，记录回收章节
    if body.get("status") == "resolved":
        fs["actual_payoff_chapter"] = body.get("actual_payoff_chapter") or data.get("last_updated_chapter", 0)
    
    data["version"] = data.get("version", 0) + 1
    _write_novel_file(novel_id, "storygraph.json", data)
    engine.memory.invalidate_all(novel_id)
    return {"ok": True, "fs_id": fs_id}


@app.post("/api/novels/{novel_id}/storygraph/foreshadows")
async def create_foreshadow(novel_id: str, body: dict):
    """创建新伏笔"""
    fs_id = body.get("id", "")
    if not fs_id:
        raise HTTPException(400, "id 为必填字段")
    
    data = _read_novel_file(novel_id, "storygraph.json")
    ledger = data.setdefault("foreshadow_ledger", {})
    
    ledger[fs_id] = {
        "id": fs_id,
        "description": body.get("description", ""),
        "planted_chapter": body.get("planted_chapter", 1),
        "planned_payoff_chapter": body.get("planned_payoff_chapter", 20),
        "actual_payoff_chapter": None,
        "status": body.get("status", "planted"),
        "hint_count": 1,
        "last_hint_chapter": body.get("planted_chapter", 1),
        "thread_id": body.get("thread_id", ""),
        "importance": body.get("importance", 3),
    }
    
    data["version"] = data.get("version", 0) + 1
    _write_novel_file(novel_id, "storygraph.json", data)
    engine.memory.invalidate_all(novel_id)
    return {"ok": True, "fs_id": fs_id}


# ── 角色编辑 ──

@app.put("/api/novels/{novel_id}/storygraph/characters/{name}")
async def update_character(novel_id: str, name: str, body: dict):
    """更新角色快照"""
    data = _read_novel_file(novel_id, "storygraph.json")
    snaps = data.setdefault("char_snapshots", {})
    
    if name not in snaps:
        snaps[name] = {
            "name": name, "last_chapter_appeared": 0,
            "current_location": "", "current_power_level": "",
            "status_effects": [], "known_secrets": [],
            "relationship_changes": [], "current_emotion": "",
            "active_goals": [],
        }
    
    snap = snaps[name]
    updatable = ("current_location", "current_emotion", "current_power_level",
                 "status_effects", "active_goals", "known_secrets")
    for key in updatable:
        if key in body:
            snap[key] = body[key]
    
    data["version"] = data.get("version", 0) + 1
    _write_novel_file(novel_id, "storygraph.json", data)
    engine.memory.invalidate_all(novel_id)
    return {"ok": True, "name": name}


# ── 快捷操作端点 ──

@app.post("/api/novels/{novel_id}/storygraph/quick-action")
async def quick_action(novel_id: str, body: dict):
    """执行快捷操作（升温/暂停/回收等）
    
    Body:
        {"type": "thread|foreshadow", "id": "...", "action": "heat_up|pause|resolve|raise_priority|lower_priority"}
    """
    action = body.get("action", "")
    item_type = body.get("type", "")
    item_id = body.get("id", "")
    
    if not action or not item_id:
        raise HTTPException(400, "action 和 id 为必填字段")
    
    data = _read_novel_file(novel_id, "storygraph.json")
    result = {"ok": True, "action": action}
    
    if item_type == "thread":
        threads = data.get("plot_threads", {})
        if item_id not in threads:
            raise HTTPException(404, "剧情线不存在")
        t = threads[item_id]
        
        if action == "heat_up":
            t["current_tension"] = min(10, t.get("current_tension", 5) + 2)
            if t.get("status") in ("active", "dormant"):
                t["status"] = "advancing"
            result["new_tension"] = t["current_tension"]
        elif action == "cool_down":
            t["current_tension"] = max(1, t.get("current_tension", 5) - 2)
            result["new_tension"] = t["current_tension"]
        elif action == "pause":
            t["status"] = "dormant"
        elif action == "resume":
            t["status"] = "active"
        elif action == "resolve":
            t["status"] = "resolved"
        elif action == "raise_priority":
            t["priority"] = min(5, t.get("priority", 3) + 1)
            result["new_priority"] = t["priority"]
        elif action == "lower_priority":
            t["priority"] = max(1, t.get("priority", 3) - 1)
            result["new_priority"] = t["priority"]
        else:
            raise HTTPException(400, f"未知操作: {action}")
    
    elif item_type == "foreshadow":
        ledger = data.get("foreshadow_ledger", {})
        if item_id not in ledger:
            raise HTTPException(404, "伏笔不存在")
        f = ledger[item_id]
        
        if action == "resolve":
            f["status"] = "resolved"
            f["actual_payoff_chapter"] = data.get("last_updated_chapter", 0)
        elif action == "delay":
            offset = body.get("offset", 5)
            f["planned_payoff_chapter"] = f.get("planned_payoff_chapter", 1) + offset
            result["new_payoff"] = f["planned_payoff_chapter"]
        elif action == "advance":
            target = body.get("target_chapter", 1)
            f["planned_payoff_chapter"] = target
            result["new_payoff"] = f["planned_payoff_chapter"]
        else:
            raise HTTPException(400, f"未知操作: {action}")
    
    else:
        raise HTTPException(400, "type 必须是 thread 或 foreshadow")
    
    data["version"] = data.get("version", 0) + 1
    _write_novel_file(novel_id, "storygraph.json", data)
    engine.memory.invalidate_all(novel_id)
    return result


def _build_character_relation_graph(data: dict) -> dict:
    """构建人物关系图数据
    
    Returns:
        {
            "nodes": [{"id": "name", "label": "name", "emotion": "...", "location": "...", 
                        "last_chapter": N, "goals": [...]}],
            "edges": [{"source": "charA", "target": "charB", "label": "关系描述", "chapter": N}]
        }
    """
    snaps = data.get("char_snapshots", {})
    nodes = []
    edges = []
    edge_set = set()  # 去重
    
    for name, snap in snaps.items():
        # 节点
        nodes.append({
            "id": name,
            "label": name,
            "emotion": snap.get("current_emotion", ""),
            "location": snap.get("current_location", ""),
            "last_chapter": snap.get("last_chapter_appeared", 0),
            "goals": snap.get("active_goals", []),
            "secrets": snap.get("known_secrets", []),
            "power_level": snap.get("current_power_level", ""),
        })
        
        # 边（从关系变化中提取）
        for rc in snap.get("relationship_changes", []):
            target = rc.get("with", "")
            if not target or target not in snaps:
                continue
            edge_key = tuple(sorted([name, target]))
            if edge_key in edge_set:
                continue
            edge_set.add(edge_key)
            edges.append({
                "source": name,
                "target": target,
                "label": rc.get("change", "关联"),
                "chapter": rc.get("chapter", 0),
            })
    
    # 补充：从剧情线的角色列表中推断关系
    threads = data.get("plot_threads", {})
    for t in threads.values():
        chars = t.get("characters", [])
        for i in range(len(chars)):
            for j in range(i + 1, len(chars)):
                edge_key = tuple(sorted([chars[i], chars[j]]))
                if edge_key not in edge_set and chars[i] in snaps and chars[j] in snaps:
                    edge_set.add(edge_key)
                    edges.append({
                        "source": chars[i],
                        "target": chars[j],
                        "label": f"共同参与: {t.get('name', '')[:12]}",
                        "chapter": 0,
                    })
    
    return {"nodes": nodes, "edges": edges}


def _build_plot_timeline(data: dict) -> dict:
    """构建剧情线时间线图数据
    
    Returns:
        {
            "lanes": [{"id": "thread_id", "name": "...", "type": "...", "status": "...",
                        "events": [{"chapter": N, "event": "...", "tension": N}],
                        "color": "#..."}],
            "causal_links": [{"from": {"thread_id": "..", "chapter": N}, "to": {...}}],
            "chapter_range": {"min": 1, "max": N}
        }
    """
    threads = data.get("plot_threads", {})
    links = data.get("causal_links", [])
    
    thread_colors = {
        "main_plot": "#f85149",
        "subplot": "#f0883e", 
        "character_arc": "#7c3aed",
        "mystery": "#58a6ff",
    }
    
    lanes = []
    for tid, t in threads.items():
        nodes = t.get("key_nodes", [])
        events = [{"chapter": n["chapter"], "event": n["event"], "tension": n.get("tension", 5)}
                  for n in sorted(nodes, key=lambda x: x["chapter"])]
        lanes.append({
            "id": tid,
            "name": t.get("name", ""),
            "type": t.get("type", "subplot"),
            "status": t.get("status", "active"),
            "priority": t.get("priority", 3),
            "events": events,
            "color": thread_colors.get(t.get("type", ""), "#8b949e"),
            "characters": t.get("characters", []),
        })
    
    # 排序：按优先级降序
    lanes.sort(key=lambda x: -x["priority"])
    
    # 计算章节范围
    all_chapters = []
    for lane in lanes:
        for e in lane["events"]:
            all_chapters.append(e["chapter"])
    for cl in links:
        all_chapters.append(cl.get("cause_chapter", 0))
        all_chapters.append(cl.get("effect_chapter", 0))
    
    ch_min = min(all_chapters) if all_chapters else 1
    ch_max = max(all_chapters) if all_chapters else 1
    
    return {
        "lanes": lanes,
        "causal_links": links,
        "chapter_range": {"min": ch_min, "max": ch_max},
    }


# ── v2.2.1: State 修复与容灾 ──

@app.post("/api/repair-states")
async def repair_all_states():
    """修复所有小说的 state.json 一致性（以磁盘章节文件为准）"""
    results = engine.memory.repair_all_states()
    return {"repaired": len(results), "details": results}


@app.post("/api/novels/{novel_id}/repair-state")
async def repair_state(novel_id: str):
    """修复指定小说的 state.json"""
    result = engine.memory.repair_state(novel_id)
    return result


@app.on_event("startup")
async def startup_repair():
    """服务器启动时自动扫描并修复 state 不一致"""
    try:
        results = engine.memory.repair_all_states()
        if results:
            log.warning(f"Startup repair: fixed state for {len(results)} novel(s)")
            for r in results:
                log.warning(f"  {r['novel_id']}: added chapters {r['added']}")
        else:
            log.info("Startup repair: all states consistent")
    except Exception as e:
        log.error(f"Startup repair failed: {e}")


if __name__ == "__main__":
    import uvicorn
    log.info(f"Starting NovelGenerator API on {HOST}:{PORT}")
    log.info(f"Storage: {NOVELS_DIR}")
    uvicorn.run("api.server:app", host=HOST, port=PORT, reload=True)
