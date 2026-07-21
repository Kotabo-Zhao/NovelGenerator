"""NovelGenerator — FastAPI Server (serves frontend + API)"""
import json
import logging
import sys
import os

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
from fastapi.responses import StreamingResponse, PlainTextResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional

from core.engine import NovelEngine
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


# ── Routes ──

@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "NovelGenerator"}


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


@app.post("/api/novels/generate")
async def generate_chapter(req: GenerateChapterRequest):
    """流式生成章节 (SSE)"""
    async def event_stream():
        async for event in engine.generate_chapter_stream(req.novel_id, req.chapter_num):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )


@app.get("/api/novels/{novel_id}/chapters/{chapter_num}")
async def get_chapter(novel_id: str, chapter_num: int):
    """获取已生成的章节内容"""
    chapters_dir = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "novels", novel_id, "chapters"
    )
    ch_file = os.path.join(chapters_dir, f"chapter_{chapter_num:04d}.md")
    if not os.path.exists(ch_file):
        raise HTTPException(status_code=404, detail="章节不存在")
    
    with open(ch_file, "r", encoding="utf-8") as f:
        content = f.read()
    return PlainTextResponse(content, media_type="text/markdown; charset=utf-8")


@app.get("/api/novels/{novel_id}/export")
async def export_novel(novel_id: str, fmt: str = "txt"):
    """导出小说"""
    content = engine.export_novel(novel_id, fmt)
    if not content:
        raise HTTPException(status_code=404, detail="导出失败")
    
    return PlainTextResponse(
        content,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={novel_id}.txt"}
    )


# ── Main ──

if __name__ == "__main__":
    import uvicorn
    log.info(f"Starting NovelGenerator API on {HOST}:{PORT}")
    uvicorn.run("server:app", host=HOST, port=PORT, reload=True)
