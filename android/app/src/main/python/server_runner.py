"""NovelGenerator Android — synchronous startup for Chaquopy."""
import os, sys, threading, traceback, time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

_STATUS_FILE = None


def _log(msg):
    if _STATUS_FILE:
        try:
            with open(_STATUS_FILE, "w") as f:
                f.write(f"{int(time.time())}|{msg}")
        except Exception:
            pass  # 状态文件写入失败不影响启动流程


def _load_llm_config(log_dir):
    """读取设置页持久化的 llm_config.json（与后端 api/server.py 同一路径约定）。"""
    try:
        with open(os.path.join(log_dir, "llm_config.json"), "r", encoding="utf-8") as f:
            import json as _json
            return _json.load(f) or {}
    except Exception:
        return {}


def start_server(api_key, host, port, log_dir, internal_build=False):
    """Synchronous: does all imports, writes progress, returns when done."""
    global _STATUS_FILE
    _STATUS_FILE = os.path.join(log_dir, "novelgen_status.txt")

    try:
        _log("init")
        # 持久化配置优先：设置页保存的 llm_config.json 覆盖原生传入的 api_key
        llm_cfg = _load_llm_config(log_dir)
        if llm_cfg.get("api_key"):
            api_key = llm_cfg["api_key"]
        os.environ["DEEPSEEK_API_KEY"] = api_key
        if llm_cfg.get("model"):
            os.environ["DEEPSEEK_MODEL"] = llm_cfg["model"]
        if internal_build:
            os.environ["NOVEL_GENERATOR_INTERNAL"] = "true"
        else:
            os.environ.pop("NOVEL_GENERATOR_INTERNAL", None)
        os.environ["NOVELGEN_WEB_DIR"] = os.path.join(_HERE, "web")
        novels_dir = os.path.join(log_dir, "novels")
        os.environ["NOVELGEN_NOVELS_DIR"] = novels_dir
        os.makedirs(novels_dir, exist_ok=True)

        _log("import_config")
        import config

        _log("import_fastapi")
        import fastapi

        _log("import_uvicorn")
        import uvicorn

        _log("import_openai")
        import openai

        _log("import_server")
        from api.server import app
        _log("app_ok")

        # Start uvicorn in daemon thread (non-blocking)
        def serve():
            try:
                config = uvicorn.Config(app, host=host, port=port, log_level="error")
                srv = uvicorn.Server(config)
                _log("server_ready")
                import asyncio
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(srv.serve())
            except Exception:
                _log(f"serve_error_{traceback.format_exc()}")

        t = threading.Thread(target=serve, daemon=True)
        t.start()

        # Wait briefly to see if it actually starts
        time.sleep(1)
        return {"status": "done"}

    except Exception:
        _log(f"error_{traceback.format_exc()}")
        return {"status": "error"}


def update_api_key(api_key):
    """Refresh the in-process provider client without exposing the key over HTTP."""
    api_key = (api_key or "").strip()
    if not api_key.startswith("sk-"):
        return {"status": "error", "message": "invalid_api_key"}
    os.environ["DEEPSEEK_API_KEY"] = api_key
    try:
        import config
        config.DEEPSEEK_API_KEY = api_key
        from api.deps import engine
        # All generators share this OpenAI client instance, so changing its
        # runtime credential updates normal and interactive generation together.
        engine.client.api_key = api_key
        _log("api_key_updated")
        return {"status": "updated"}
    except Exception:
        _log(f"api_key_update_error_{traceback.format_exc()}")
        return {"status": "error", "message": "client_update_failed"}
