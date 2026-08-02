"""NovelGenerator — FastAPI Server (serves frontend + API)

2026-07-31 重构: 71 个业务路由按域拆分到 api/routers/（novels/outline/quality/
storygraph/requirements/styles/trends/xhs），本文件只保留应用装配、中间件、
静态资源服务与健康检查。
"""
import json
import asyncio
import logging
import sys
import os
import time as _time
import urllib.parse
from collections import defaultdict

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

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, PlainTextResponse, FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from config import CORS_ORIGINS, HOST, PORT, NOVELS_DIR, DEFAULT_CHAPTER_WORDS

# 共享依赖：engine 单例 + log（见 deps.py）
from .deps import engine, log
from .routers import novels, outline, quality, storygraph, requirements, styles, trends, xhs, characters, feedback, tts, interactive

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

app = FastAPI(title="NovelGenerator API", version="1.0.0")
# CORS 来源统一走 config（逗号分隔），与 Android 副本保持一致；
# wildcard + credentials 是浏览器禁止的组合，通配时自动关闭 credentials
_cors_origins = CORS_ORIGINS.split(",") if isinstance(CORS_ORIGINS, str) else CORS_ORIGINS
_cors_origins = [o.strip() for o in _cors_origins if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if "*" in _cors_origins else _cors_origins,
    allow_credentials="*" not in _cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Rate Limiting Middleware ──

_rate_limits: dict = defaultdict(list)  # {ip: [timestamps]}
_RATE_WINDOW = 60  # 1 minute window
_RATE_MAX_REQUESTS = int(os.getenv("RATE_LIMIT", "60"))  # 60 req/min per IP
# 生成端点是 SSE 长连接（单章 60-90s），用"并发连接数"而非"次数/min"限流：
# 同一 IP 同时最多 N 个生成连接，连接释放即恢复，避免连点/刷新被窗口计数锁死
_RATE_GENERATE_MAX_CONCURRENT = int(os.getenv("RATE_LIMIT_GENERATE_CONCURRENT", "3"))

_generate_conns: dict = defaultdict(int)  # {ip: 当前并发生成连接数}


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """内存速率限制：普通端点按 60s 窗口计数，生成端点按并发连接数"""
    client_ip = request.client.host if request.client else "unknown"
    is_generate = "/generate" in request.url.path

    if is_generate:
        # 生成端点：并发连接限流（连接结束即释放）
        if _generate_conns[client_ip] >= _RATE_GENERATE_MAX_CONCURRENT:
            log.warning("生成并发限制触发: IP=%s, path=%s, conns=%d",
                        client_ip, request.url.path, _generate_conns[client_ip])
            return JSONResponse(
                status_code=429,
                content={"detail": f"已有生成任务正在进行，请等待完成后再试（当前 {_generate_conns[client_ip]} 个并发）",
                         "retry_after": 10},
            )
        _generate_conns[client_ip] += 1
        try:
            return await call_next(request)
        finally:
            _generate_conns[client_ip] = max(0, _generate_conns[client_ip] - 1)

    # 普通端点：60 秒窗口计数
    # v3.5.17: 静态资源不限流（favicon/sw.js/vue.js 页面加载即消耗多次，
    # 计入 60/min 窗口会误伤正常使用）；只对 /api 接口限流
    if not request.url.path.startswith("/api/"):
        return await call_next(request)

    now = _time.time()
    window_start = now - _RATE_WINDOW
    _rate_limits[client_ip] = [t for t in _rate_limits[client_ip] if t > window_start]

    if len(_rate_limits[client_ip]) >= _RATE_MAX_REQUESTS:
        log.warning("速率限制触发: IP=%s, path=%s, count=%d", client_ip, request.url.path, len(_rate_limits[client_ip]))
        return JSONResponse(
            status_code=429,
            content={"detail": f"请求过于频繁，请 {_RATE_WINDOW} 秒后再试", "retry_after": _RATE_WINDOW},
        )

    _rate_limits[client_ip].append(now)
    return await call_next(request)


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
            return HTMLResponse(
                f.read(),
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
            )
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
        return FileResponse(
            path, media_type="application/javascript",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
        )
    return Response("// sw.js not found", media_type="application/javascript", status_code=404)


@app.get("/manifest.json")
async def serve_manifest():
    path = os.path.join(WEB_DIR, "manifest.json")
    if os.path.exists(path):
        return FileResponse(path, media_type="application/json")
    return {}


# ── Health ──

@app.get("/api/health")
async def health():
    novels_exist = os.path.exists(NOVELS_DIR)
    novel_count = len([f for f in os.listdir(NOVELS_DIR) if os.path.isdir(os.path.join(NOVELS_DIR, f)) and f != ".gitkeep"]) if novels_exist else 0
    # 列出所有 novel 目录和对应的书名
    novel_list = []
    if novels_exist:
        for d in sorted(os.listdir(NOVELS_DIR)):
            dpath = os.path.join(NOVELS_DIR, d)
            if os.path.isdir(dpath) and d != ".gitkeep":
                plan_f = os.path.join(dpath, "plan.json")
                title = d
                if os.path.exists(plan_f):
                    try:
                        with open(plan_f, "r", encoding="utf-8") as f:
                            pd = json.load(f)
                        title = pd.get("title", d) if isinstance(pd, dict) else d
                    except Exception as _e:
                        log.warning("读取 plan.json 失败 %s: %s", plan_f, _e)
                novel_list.append({"dir": d, "title": title, "has_plan": os.path.exists(plan_f)})
    return {
        "status": "ok",
        "service": "NovelGenerator",
        "storage": NOVELS_DIR,
        "storage_exists": novels_exist,
        "novel_count": novel_count,
        "novels": novel_list,
    }


# ── 业务路由挂载（2026-07-31 按域拆分）──

for _router in (novels, outline, quality, storygraph, requirements, styles, trends, xhs, characters, feedback, tts, interactive):
    app.include_router(_router.router)


# ── 启动时状态修复 ──

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
    uvicorn.run("api.server:app", host=HOST, port=PORT, reload=False)
