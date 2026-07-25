"""NovelGenerator Android server — Chaquopy entry point.

Starts the FastAPI server in a background thread when called from Kotlin.
"""
import os
import sys
import threading
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("NovelGen-Android")

# Android paths: Chaquopy extracts Python files to getApplicationInfo().nativeLibraryDir
_HERE = os.path.dirname(os.path.abspath(__file__))
_WEB_DIR = os.path.join(_HERE, "web")
_NOVELS_DIR = os.path.join(os.environ.get("EXTERNAL_STORAGE", _HERE), "NovelGenerator", "novels")

os.makedirs(_NOVELS_DIR, exist_ok=True)
sys.path.insert(0, _HERE)

# Override config before importing server
os.environ["NOVELGEN_NOVELS_DIR"] = _NOVELS_DIR
os.environ["NOVELGEN_WEB_DIR"] = _WEB_DIR


_server_started = False
_server_ready = False
_server_error = None


def start_server(api_key: str, host: str = "127.0.0.1", port: int = 8899):
    """Start the FastAPI uvicorn server in a background thread."""
    global _server_started, _server_ready, _server_error

    if _server_started:
        return {"status": "already_running"}

    _server_started = True
    os.environ["DEEPSEEK_API_KEY"] = api_key
    os.environ["HOST"] = host
    os.environ["PORT"] = str(port)

    def run():
        global _server_ready, _server_error
        try:
            import uvicorn
            from api.server import app

            log.info("Starting server on %s:%d", host, port)
            log.info("Novels dir: %s", _NOVELS_DIR)
            log.info("Web dir: %s", _WEB_DIR)

            # Signal ready in a moment
            def on_startup():
                global _server_ready
                _server_ready = True
                log.info("Server ready!")

            config = uvicorn.Config(
                app,
                host=host,
                port=port,
                log_level="info",
            )
            server = uvicorn.Server(config)

            # Start server (blocking in this thread)
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.call_soon(on_startup)
            loop.run_until_complete(server.serve())

        except Exception as e:
            _server_error = str(e)
            log.exception("Server failed: %s", e)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return {"status": "starting"}


def get_server_status():
    """Return server status for Kotlin to poll."""
    return {
        "started": _server_started,
        "ready": _server_ready,
        "error": _server_error,
    }
