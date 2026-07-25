"""NovelGenerator Android server — Chaquopy entry point."""
import os
import sys
import threading
import logging
import traceback

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("NovelGen-Android")

_HERE = os.path.dirname(os.path.abspath(__file__))
_WEB_DIR = os.path.join(_HERE, "web")
_NOVELS_DIR = os.path.join(_HERE, "novels")

os.makedirs(_NOVELS_DIR, exist_ok=True)
sys.path.insert(0, _HERE)

_server_started = False
_server_ready = False
_server_error = None


def start_server(api_key, host, port):
    global _server_started, _server_ready, _server_error
    if _server_started:
        return {"status": "already_running"}
    _server_started = True

    os.environ["DEEPSEEK_API_KEY"] = api_key
    os.environ["NOVELGEN_NOVELS_DIR"] = _NOVELS_DIR
    os.environ["NOVELGEN_WEB_DIR"] = _WEB_DIR

    def run():
        global _server_ready, _server_error
        try:
            log.info("Importing server modules...")
            import uvicorn
            from api.server import app
            log.info("Modules loaded, starting uvicorn on %s:%d", host, port)

            config = uvicorn.Config(app, host=host, port=port, log_level="info")
            server = uvicorn.Server(config)
            _server_ready = True
            log.info("Server ready!")

            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(server.serve())
        except Exception as e:
            _server_error = traceback.format_exc()
            log.error("Server failed: %s", _server_error)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return {"status": "starting"}


def get_server_status():
    return {
        "started": _server_started,
        "ready": _server_ready,
        "error": _server_error,
    }
