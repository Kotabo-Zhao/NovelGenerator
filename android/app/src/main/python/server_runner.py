"""NovelGenerator Android — Chaquopy entry.
Two-phase startup: synchronous import test → async server start."""
import os, sys, threading, traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

STATUS = {"step": "", "ready": False, "error": None}


def quick_test(api_key):
    """Synchronous import chain test. Called from main thread."""
    try:
        STATUS["step"] = "env"
        os.environ["DEEPSEEK_API_KEY"] = api_key
        os.environ["NOVELGEN_WEB_DIR"] = os.path.join(_HERE, "web")
        os.environ["NOVELGEN_NOVELS_DIR"] = os.path.join(_HERE, "novels")
        os.makedirs(os.environ["NOVELGEN_NOVELS_DIR"], exist_ok=True)

        STATUS["step"] = "config"
        import config

        STATUS["step"] = "fastapi"
        import fastapi

        STATUS["step"] = "uvicorn"
        import uvicorn

        STATUS["step"] = "openai"
        import openai

        STATUS["step"] = "core_atomic"
        from core.atomic_io import atomic_write_json, safe_read_json

        STATUS["step"] = "core_shared_memory"
        from core.shared_memory import SharedMemoryManager

        STATUS["step"] = "core_engine"
        from core.engine import NovelEngine
        engine = NovelEngine()

        STATUS["step"] = "api_server"
        from api.server import app
        STATUS["step"] = f"OK routes={len(app.routes)}"
        STATUS["ready"] = True

        return {"ok": True, "step": STATUS["step"]}

    except Exception:
        STATUS["error"] = traceback.format_exc()
        return {"ok": False, "error": STATUS["error"], "step": STATUS["step"]}


def get_status():
    return STATUS


def start_uvicorn(host="127.0.0.1", port=8899):
    """Start uvicorn in a daemon thread. Call after quick_test passes."""
    try:
        import uvicorn
        from api.server import app

        def serve():
            try:
                config = uvicorn.Config(app, host=host, port=port, log_level="info")
                server = uvicorn.Server(config)
                import asyncio
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(server.serve())
            except Exception:
                STATUS["error"] = traceback.format_exc()

        t = threading.Thread(target=serve, daemon=True)
        t.start()
        return {"status": "ok"}
    except Exception:
        return {"status": "error", "error": traceback.format_exc()}
