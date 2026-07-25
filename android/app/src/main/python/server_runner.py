"""NovelGenerator Android — file-based progress logging."""
import os, sys, threading, traceback, time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

# Will be set by start_server
_STATUS_FILE = None


def _log(msg):
    """Write to status file so Kotlin can read it."""
    global _STATUS_FILE
    if _STATUS_FILE:
        try:
            with open(_STATUS_FILE, "w") as f:
                f.write(f"{time.time():.0f}|{msg}")
        except:
            pass


def start_server(api_key, host, port, log_dir):
    """Entry point. Sets up env, starts import chain in a thread."""
    global _STATUS_FILE
    _STATUS_FILE = os.path.join(log_dir, "novelgen_status.txt")
    _log("init")

    os.environ["DEEPSEEK_API_KEY"] = api_key
    os.environ["NOVELGEN_WEB_DIR"] = os.path.join(_HERE, "web")
    novels_dir = os.path.join(log_dir, "novels")
    os.environ["NOVELGEN_NOVELS_DIR"] = novels_dir
    os.makedirs(novels_dir, exist_ok=True)

    def run():
        try:
            _log("import_config")
            import config
            _log(f"import_fastapi")
            import fastapi
            _log("import_uvicorn")
            import uvicorn
            _log("import_openai")
            import openai
            _log("import_core")
            from core.engine import NovelEngine
            engine = NovelEngine()
            _log(f"engine_ok_{engine.model}")
            from api.server import app
            _log(f"app_ok_{len(app.routes)}")

            # Start uvicorn
            cfg = uvicorn.Config(app, host=host, port=port, log_level="info")
            srv = uvicorn.Server(cfg)
            _log("server_ready")

            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(srv.serve())
        except Exception:
            _log(f"error_{traceback.format_exc()}")

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return {"status": "started"}


def get_status():
    """Read status file."""
    global _STATUS_FILE
    if _STATUS_FILE and os.path.exists(_STATUS_FILE):
        try:
            with open(_STATUS_FILE) as f:
                return f.read().strip()
        except:
            pass
    return "waiting"
