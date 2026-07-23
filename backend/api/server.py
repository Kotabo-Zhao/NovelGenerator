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

app = FastAPI(title="NovelGenerator API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = NovelEngine()

# 前端文件目录 — 使用 abspath 防止 __file__ 为相对路径时解析错误
_WEB_BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WEB_DIR = os.path.join(_WEB_BASE, "web")


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
            log.exception("SSE producer crashed")
            await q.put(("error", str(e)))
        await q.put(("done", None))
    
    async def heartbeater():
        for t in range(60):  # max 480s
            await asyncio.sleep(8)
            if cancelled:
                break
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
