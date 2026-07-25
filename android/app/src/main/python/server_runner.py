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
        except:
            pass


def start_server(api_key, host, port, log_dir):
    """Synchronous: does all imports, writes progress, returns when done."""
    global _STATUS_FILE
    _STATUS_FILE = os.path.join(log_dir, "novelgen_status.txt")

    try:
        _log("init")
        os.environ["DEEPSEEK_API_KEY"] = api_key
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

        _log("import_core")
        from core.engine import NovelEngine
        engine = NovelEngine()
        _log("engine_ok")

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
