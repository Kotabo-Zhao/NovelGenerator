import asyncio
import json
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

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from config import CORS_ORIGINS, HOST, PORT, NOVELS_DIR, LLM_CONFIG_FILE
from core.path_security import validate_novel_id, RESERVED_NOVEL_IDS

# 共享依赖：engine 单例 + log（见 deps.py）
from .deps import engine, log, ownership_store
from .auth import owner_id_from_authorization, is_internal_version
from .middleware import RequestLimits, NovelWorldJobs
from core.auth import AuthError
from .routers import auth, novels, v1, outline, quality, requirements, styles, trends, characters, feedback, tts, bible, ideas, roleplay, system, interactive

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

app = FastAPI(title="NovelGenerator API", version="1.0.0")
app.add_middleware(NovelWorldJobs)
app.add_middleware(RequestLimits)
# CORS 来源统一走 config（逗号分隔），与 Android 副本保持一致；
# wildcard + credentials 是浏览器禁止的组合，通配时自动关闭 credentials
_cors_origins = CORS_ORIGINS.split(",") if isinstance(CORS_ORIGINS, str) else CORS_ORIGINS
_cors_origins = [o.strip() for o in _cors_origins if o.strip()]
_allow_wildcard_cors = os.getenv("ALLOW_WILDCARD_CORS", "false").lower() == "true"
if "*" in _cors_origins and not _allow_wildcard_cors:
    log.warning("Wildcard CORS is disabled by default; configure explicit CORS_ORIGINS for public deployments")
    _cors_origins = []
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if "*" in _cors_origins else _cors_origins,
        allow_credentials="*" not in _cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# NOVEL_GENERATOR_INTERNAL 是全局鉴权后门：仅在绑定本机回环时允许。
# 绑定非回环地址却开启该开关，等于把全站数据零隔离暴露出去。
# 模块加载时仅告警；启动时（startup 事件）若仍为非回环 + INTERNAL，则拒绝启动
# （除非显式设置 NOVEL_GENERATOR_INTERNAL_NON_LOOPBACK=1 确认风险）。
if is_internal_version() and HOST not in ("127.0.0.1", "localhost", "::1"):
    log.critical(
        "NOVEL_GENERATOR_INTERNAL=true 且 HOST=%s 非本机回环——鉴权被全局旁路，"
        "生产部署将导致所有用户数据零隔离。请设置 NOVEL_GENERATOR_INTERNAL=false。",
        HOST,
    )


MAX_REQUEST_BYTES = int(os.getenv("MAX_REQUEST_BYTES", str(16_000_000)))


@app.middleware("http")
async def request_size_middleware(request: Request, call_next):
    """Reject oversized bodies before Pydantic or JSON parsing allocates them."""
    raw_length = request.headers.get("content-length")
    try:
        content_length = int(raw_length) if raw_length is not None else 0
    except ValueError:
        return JSONResponse(status_code=400, content={"detail": "Content-Length 无效"})
    if content_length > MAX_REQUEST_BYTES:
        return JSONResponse(
            status_code=413,
            content={"detail": f"请求体过大，最多 {MAX_REQUEST_BYTES} 字节"},
        )
    return await call_next(request)


@app.middleware("http")
async def validate_novel_path_middleware(request: Request, call_next):
    """在所有作品路由进入业务层前统一阻断危险的目录名。

    注意：/api/novels/create-stream、/api/novels/generate 等集合动作的
    第一段是路由名而非 novel_id，必须先跳过保留词，否则会被误拦为
    "非法小说 ID"（保留词的 IDOR 防护由 legacy_novel_owner_middleware 承担）。
    """
    prefix = "/api/novels/"
    path = request.url.path
    if path.startswith(prefix):
        raw_novel_id = urllib.parse.unquote(path[len(prefix):].split("/", 1)[0])
        if raw_novel_id and raw_novel_id not in RESERVED_NOVEL_IDS:
            try:
                validate_novel_id(raw_novel_id)
            except ValueError:
                return JSONResponse(status_code=400, content={"detail": "非法小说 ID"})
    return await call_next(request)


@app.middleware("http")
async def legacy_novel_owner_middleware(request: Request, call_next):
    """认证并隔离旧 `/api/novels` 兼容面，避免绕过 v1 owner 检查。"""
    path = request.url.path
    if path != "/api/novels" and not path.startswith("/api/novels/"):
        return await call_next(request)

    try:
        owner_id = owner_id_from_authorization(request.headers.get("authorization"))
    except AuthError:
        return JSONResponse(
            status_code=401,
            content={"detail": "需要微信登录"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    request.state.owner_id = owner_id
    if is_internal_version():
        return await call_next(request)
    suffix = path[len("/api/novels"):].strip("/")
    first_segment = urllib.parse.unquote(suffix.split("/", 1)[0]) if suffix else ""
    # 集合动作 = path_security.RESERVED_NOVEL_IDS（单一事实源）：
    # 这些是旧接口的集合级动作，具体作品 ID 位于 body/query 中，
    # 由对应 handler 继续执行 owner 校验。集合动作全部是 POST；
    # 对非 POST 命中这些保留词一律按作品资源校验归属，杜绝同名 IDOR。
    is_collection_action = first_segment in RESERVED_NOVEL_IDS and request.method == "POST"
    if first_segment and not is_collection_action:
        try:
            validate_novel_id(first_segment)
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "非法小说 ID"})
        if not ownership_store.owns(first_segment, owner_id):
            # 对不存在和不属于当前用户的作品统一返回 404，避免枚举资源。
            return JSONResponse(status_code=404, content={"detail": "小说不存在"})

    return await call_next(request)

# ── Rate Limiting Middleware ──

def _client_ip(request: Request) -> str:
    from .middleware import client_ip
    return client_ip(request.scope)


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """为 API 和页面统一补充不依赖前端框架的基础安全响应头。"""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    if request.url.scheme == "https" or forwarded_proto == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


# 前端文件目录 — 使用 abspath 防止 __file__ 为相对路径时解析错误
# PC: __file__ = .../backend/api/server.py, web/ 在 .../web/ (3层)
# Android: __file__ = .../api/server.py, web/ 在 .../web/ (2层)
_api_dir = os.path.dirname(os.path.abspath(__file__))
_python_root = os.path.dirname(_api_dir)
_pc_root = os.path.dirname(_python_root)
_web_dir_2 = os.path.join(_python_root, "web")
_web_dir_3 = os.path.join(_pc_root, "web")
if os.path.isdir(_web_dir_3):
    WEB_DIR = _web_dir_3
elif os.path.isdir(_web_dir_2):
    WEB_DIR = _web_dir_2
else:
    WEB_DIR = os.getenv("WEB_DIR", _web_dir_2)

_assets_dir = os.path.join(WEB_DIR, "assets")
if os.path.isdir(_assets_dir):
    app.mount("/assets", StaticFiles(directory=_assets_dir), name="assets")


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


@app.get("/roleplay.html", response_class=HTMLResponse)
async def serve_roleplay():
    path = os.path.join(WEB_DIR, "roleplay.html")
    if os.path.isfile(path):
        return FileResponse(path, media_type="text/html", headers={"Cache-Control": "no-cache"})
    return HTMLResponse("<h1>角色扮演页面不存在</h1>", status_code=404)


@app.get("/scene.html", response_class=HTMLResponse)
async def serve_novel_world():
    path = os.path.join(WEB_DIR, "scene.html")
    if os.path.isfile(path):
        return FileResponse(path, media_type="text/html", headers={"Cache-Control": "no-cache, no-store"})
    return HTMLResponse("<h1>小说世界页面不存在</h1>", status_code=404)


@app.get('/cards.html', response_class=HTMLResponse)
async def serve_character_space():
    return FileResponse(os.path.join(WEB_DIR, 'cards.html'), media_type='text/html',
                        headers={'Cache-Control': 'no-cache'})


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

# 保留模块级别旧名字，避免本地脚本和旧测试导入时报错。
_LLM_CONFIG_FILE = LLM_CONFIG_FILE


def _load_llm_config_file() -> dict:
    try:
        with open(_LLM_CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_llm_config_file(cfg: dict) -> None:
    """原子保存本地运行时配置，避免进程中断留下半份 JSON。"""
    tmp_path = f"{_LLM_CONFIG_FILE}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, _LLM_CONFIG_FILE)
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass


@app.get("/api/health")
async def health():
    novels_exist = os.path.exists(NOVELS_DIR)
    novel_count = 0
    if novels_exist:
        novel_count = sum(
            1 for name in os.listdir(NOVELS_DIR)
            if not name.startswith(".")
            and os.path.isdir(os.path.join(NOVELS_DIR, name))
            and os.path.exists(os.path.join(NOVELS_DIR, name, "plan.json"))
        )
    current = engine.llm_config()
    return {
        "status": "ok",
        "service": "NovelGenerator",
        "app_version": "4.3.1",
        "workspace_id": __import__('hashlib').sha256(os.path.normcase(os.path.abspath(NOVELS_DIR)).encode()).hexdigest()[:12],
        "storage_exists": novels_exist,
        "novel_count": novel_count,
        "llm_configured": current["api_key_configured"],
        "provider": current["provider"],
        "provider_name": current["provider_name"],
        "model": current["model"],
        "allowed_models": ["deepseek-chat", "deepseek-v4-flash"],
        "models": current["models"],
    }


# ── LLM 运行时配置（设置页：API Key + 模型切换） ──

@app.get("/api/config/llm")
async def get_llm_config():
    """读取当前 LLM 配置状态（不泄露 key 明文）。"""
    return engine.llm_config()


@app.get("/api/config/llm/providers")
async def get_llm_providers():
    """返回前端的一键配置目录，不包含任何用户密钥。"""
    return {"providers": engine.provider_catalog()}


@app.post("/api/config/llm")
async def set_llm_config(req: dict):
    """运行时更新供应商、Base URL、API Key 和模型；持久化到本地文件。

    仅允许本机回环来源调用（Android 内嵌版恒为 127.0.0.1，公网部署不会暴露）。
    旧版只提交 api_key/model 的请求仍按 DeepSeek 兼容路径处理。
    """
    if not isinstance(req, dict):
        raise HTTPException(status_code=400, detail="配置格式无效")
    from .middleware import active_generations
    if active_generations() or roleplay._store._generating:
        raise HTTPException(status_code=409, detail="请先停止当前生成，再修改模型连接")
    provider = (req.get("provider") or "").strip().lower()
    api_key = (req.get("api_key") or "").strip()
    base_url = (req.get("base_url") or "").strip()
    model = (req.get("model") or "").strip()
    has_new_fields = any((provider, base_url))
    if not api_key and not model and not has_new_fields:
        raise HTTPException(status_code=400, detail="需要提供 api_key 或 model 至少一项")

    applied: list[str] = []
    try:
        # 兼容旧版设置页：没有 provider/base_url 时保留原有严格的 DeepSeek
        # 模型与 sk- Key 校验；新设置页走统一供应商合同。
        if not has_new_fields and not provider:
            if api_key and not engine.set_api_key(api_key):
                raise HTTPException(status_code=400, detail="API Key 格式无效（旧版 DeepSeek Key 应以 sk- 开头）")
            if model and not engine.set_model(model):
                raise HTTPException(status_code=400, detail="不支持的模型（可选: deepseek-chat / deepseek-v4-flash）")
            if api_key:
                applied.append("api_key")
            if model:
                applied.append("model")
        else:
            before = engine.llm_config()
            engine.set_provider_config(
                provider or before["provider"],
                api_key=api_key or None,
                base_url=base_url or None,
                model=model or None,
            )
            if provider and provider != before["provider"]:
                applied.append("provider")
            if api_key:
                applied.append("api_key")
            if base_url:
                applied.append("base_url")
            if model:
                applied.append("model")
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    cfg = _load_llm_config_file()
    current = engine.llm_config()
    cfg.update({
        "provider": current["provider"],
        "base_url": current["base_url"],
        "model": current["model"],
    })
    # 仅在已经配置过或本次提交了 Key 时写入，避免把空占位值误当成凭据。
    if current["api_key_configured"] or api_key:
        cfg["api_key"] = engine.api_key
    _save_llm_config_file(cfg)
    return {"status": "ok", "applied": applied or ["config"], "config": current}


@app.post("/api/config/llm/test")
async def test_llm_config():
    """使用当前配置发起极小请求，供设置页的“保存并测试”使用。"""
    try:
        result = await asyncio.to_thread(engine.test_connection)
        return {"status": "ok", "result": result}
    except Exception as exc:
        # 只返回可操作的通用文案，供应商响应可能包含请求头/内部 URL。
        log.warning("LLM connection test failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="模型连接失败，请检查 API Key、模型和网络") from exc


# ── 业务路由挂载（2026-07-31 按域拆分）──

for _router in (auth, v1, novels, outline, quality, requirements, styles, trends, characters, feedback, tts, bible, ideas, roleplay, system, interactive):
    app.include_router(_router.router)


# ── 启动时状态修复 ──

@app.on_event("startup")
async def startup_repair():
    """服务器启动时自动扫描并修复 state 不一致"""
    # v3.8: 拒绝启动——INTERNAL 鉴权后门 + 非回环绑定 = 全站数据零隔离。
    # 仅告警不够：生产误配时应有硬失败。显式设置 NOVEL_GENERATOR_INTERNAL_NON_LOOPBACK=1
    # 可确认风险后放行（仅限本机/局域网调试，Android 内嵌版恒为 127.0.0.1 不受影响）。
    if (is_internal_version()
            and HOST not in ("127.0.0.1", "localhost", "::1")
            and os.getenv("NOVEL_GENERATOR_INTERNAL_NON_LOOPBACK", "").strip().lower()
            not in {"1", "true", "yes", "on"}):
        raise RuntimeError(
            "拒绝启动：NOVEL_GENERATOR_INTERNAL=true 且 HOST 非本机回环。"
            "这会全局旁路鉴权、暴露所有用户数据。请设置 NOVEL_GENERATOR_INTERNAL=false；"
            "仅在知晓风险的调试场景可设 NOVEL_GENERATOR_INTERNAL_NON_LOOPBACK=1 强制放行。"
        )
    # v3.6.4: 确保 NOVELS_DIR 存在（Android 旧 APK 的 server_runner 可能未建目录）
    try:
        import os as _os
        _os.makedirs(NOVELS_DIR, exist_ok=True)
    except Exception as _e:
        log.warning(f"NOVELS_DIR 创建失败: {_e}")
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
    try:
        await v1.recover_pending_jobs()
    except Exception as e:
        log.error(f"Startup job recovery failed: {e}")
    # v3.8: 周期任务恢复——requeue_stale 此前只在启动时跑一次，
    # 进程存活期间卡死的 RUNNING 任务会永久占用 max_active 名额
    try:
        v1.start_periodic_recovery()
    except Exception as e:
        log.error(f"Periodic job recovery start failed: {e}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.server:app", host=HOST, port=PORT, reload=False)
