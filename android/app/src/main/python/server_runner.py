"""NovelGenerator v5 Android — synchronous startup bridge for Chaquopy."""
import asyncio
import os
import sys
import threading
import time
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

_STATUS_FILE = None
_SERVER_THREAD = None
_APP = None


def _log(msg: str) -> None:
    if _STATUS_FILE:
        try:
            with open(_STATUS_FILE, "w", encoding="utf-8") as f:
                f.write(f"{int(time.time())}|{msg}")
        except Exception:
            pass


def start_server(api_key, host, port, log_dir, internal_build=False):
    """Boot NovelGenerator v5 FastAPI + SQLite WAL/FTS5 server inside Android."""
    global _STATUS_FILE, _SERVER_THREAD, _APP
    _STATUS_FILE = os.path.join(log_dir, "novelgen_status.txt")
    if _SERVER_THREAD is not None and _SERVER_THREAD.is_alive():
        _log("server_ready")
        return {"status": "done"}

    try:
        _log("init")
        os.environ["NOVELGEN_DEFER_APP"] = "1"
        os.environ["NOVELGEN_DATA_DIR"] = log_dir
        os.environ["NOVELGEN_FRONTEND_DIR"] = os.path.join(_HERE, "frontend")

        import uvicorn
        from backend.app import create_app

        _APP = create_app(data_dir=log_dir)
        if api_key and str(api_key).strip():
            current = _APP.state.llm_store.load()
            if not current.api_key:
                _APP.state.llm_store.save({"mode": "openai_compatible", "api_key": str(api_key).strip()})
        _log("app_ok")

        ready = threading.Event()
        failure = []

        class LocalServer(uvicorn.Server):
            async def startup(self, sockets=None):
                await super().startup(sockets=sockets)
                if self.started:
                    _log("server_ready")
                    ready.set()

        def serve():
            try:
                cfg = uvicorn.Config(_APP, host=host, port=port, log_level="error", loop="asyncio", http="h11", ws="none")
                srv = LocalServer(cfg)
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(srv.serve())
                finally:
                    loop.close()
            except BaseException:
                failure.append(traceback.format_exc())
                _log(f"error_{failure[-1]}")
                ready.set()

        _SERVER_THREAD = threading.Thread(target=serve, daemon=True)
        _SERVER_THREAD.start()

        if not ready.wait(timeout=30):
            _log("error_本地服务启动超时，请重新打开应用")
            return {"status": "error"}
        if failure:
            return {"status": "error"}
        return {"status": "done"}
    except Exception as exc:
        tb_lines = [ln.strip() for ln in traceback.format_exc().splitlines() if ln.strip()]
        tail = " | ".join(tb_lines[-3:])
        _log(f"error_{type(exc).__name__}: {exc} ({tail})")
        return {"status": "error"}


def update_api_key(api_key):
    """Update persisted LLM API key in-process."""
    key = (api_key or "").strip()
    if not key:
        return {"status": "error", "message": "empty_api_key"}
    try:
        if _APP is not None:
            _APP.state.llm_store.save({"mode": "openai_compatible", "api_key": key})
            _log("api_key_updated")
            return {"status": "updated"}
        return {"status": "error", "message": "server_not_started"}
    except Exception:
        _log(f"api_key_update_error_{traceback.format_exc()}")
        return {"status": "error", "message": "client_update_failed"}
