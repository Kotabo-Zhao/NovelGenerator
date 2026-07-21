"""NovelGenerator — FastAPI Server (serves frontend + API)"""
import json
import logging
import sys
import os

# 提高递归深度限制，防止大型 JSON 解析时触发 RecursionError
sys.setrecursionlimit(10000)

# Load .env from project root (local dev only; Render uses env vars)
try:
    from dotenv import load_dotenv
    _ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    _dotenv_path = os.path.join(_ROOT, ".env")
    if os.path.exists(_dotenv_path):
        load_dotenv(_dotenv_path)
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, PlainTextResponse, FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional

from core.engine import NovelEngine
from core.pacing_checker import PacingChecker
from core.style_fingerprint import StyleFingerprint
from config import CORS_ORIGINS, HOST, PORT

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

# 前端文件目录
WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "web")


# ── Frontend Route ──

@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    """Serve the SPA frontend"""
    index_path = os.path.join(WEB_DIR, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return f.read()
    return HTMLResponse("<h1>NovelGenerator</h1><p>Frontend not found</p>", status_code=404)


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


class GenerateChapterRequest(BaseModel):
    novel_id: str
    chapter_num: int
    writing_mode: str = "webnovel"  # "webnovel" | "literary"
    feedback: Optional[str] = None  # 用户修改意见（重生成场景）


# ── Routes ──

@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "NovelGenerator"}


@app.get("/api/styles")
async def get_styles():
    """返回所有可用风格（分组）"""
    from core.styles import get_style_categories, STYLES
    categories = get_style_categories()
    # 附带简短的作者标签
    result = {}
    for cat, names in categories.items():
        result[cat] = [{"name": n, "author": STYLES[n]["author"]} for n in names]
    return {"categories": result}


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
    """创建新小说 — 灵感 → 世界观+角色+大纲"""
    try:
        plan = engine.create_novel({
            "genre": req.genre,
            "style": req.style,
            "inspiration": req.inspiration,
            "target_words": req.target_words,
            "title": req.title,
        })
        return {"success": True, "novel": plan}
    except Exception as e:
        log.exception("Failed to create novel")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/novels/create-stream")
async def create_novel_stream(req: CreateNovelRequest):
    """流式创建新小说 — 三阶段进度条"""
    async def event_stream():
        try:
            async for event in engine.create_novel_stream({
                "genre": req.genre,
                "style": req.style,
                "inspiration": req.inspiration,
                "target_words": req.target_words,
                "title": req.title,
            }):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as e:
            log.exception("create_novel_stream crashed")
            yield f"data: {json.dumps({'type':'error','message':str(e)}, ensure_ascii=False)}\n\n"
    
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@app.post("/api/novels/generate")
async def generate_chapter(req: GenerateChapterRequest):
    """流式生成章节 (SSE)"""
    async def event_stream():
        try:
            async for event in engine.generate_chapter_stream(
                req.novel_id, req.chapter_num, req.writing_mode,
                feedback=req.feedback,
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as e:
            log.exception("generate_chapter crashed")
            yield f"data: {json.dumps({'type':'error','message':str(e)}, ensure_ascii=False)}\n\n"
    
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
                try:
                    async for event in engine.generate_chapter_stream(
                        novel_id, ch_num, writing_mode
                    ):
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    yield f"data: {json.dumps({'type':'chapter_done','chapter':ch_num}, ensure_ascii=False)}\n\n"
                except Exception as ch_err:
                    log.warning(f"Batch chapter {ch_num} failed: {ch_err}, skipping")
                    failed.append(ch_num)
                    yield f"data: {json.dumps({'type':'chapter_failed','chapter':ch_num,'error':str(ch_err)}, ensure_ascii=False)}\n\n"
                    continue
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
    
    return PlainTextResponse(
        content,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={novel_id}.txt"}
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

if __name__ == "__main__":
    import uvicorn
    log.info(f"Starting NovelGenerator API on {HOST}:{PORT}")
    uvicorn.run("api.server:app", host=HOST, port=PORT, reload=True)
