"""NovelGenerator Android — synchronous startup for Chaquopy."""
import os, sys, threading, traceback, time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

_STATUS_FILE = None
_SERVER_THREAD = None


def _log(msg):
    if _STATUS_FILE:
        try:
            with open(_STATUS_FILE, "w", encoding="utf-8") as f:
                f.write(f"{int(time.time())}|{msg}")
        except Exception:
            pass  # 状态文件写入失败不影响启动流程


def start_server(api_key, host, port, log_dir):
    """Synchronous: does all imports, writes progress, returns when done."""
    global _STATUS_FILE, _SERVER_THREAD
    _STATUS_FILE = os.path.join(log_dir, "novelgen_status.txt")
    if _SERVER_THREAD is not None and _SERVER_THREAD.is_alive():
        _log("server_ready")
        return {"status": "done"}

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

        _log("import_server")
        from api.server import app
        _log("app_ok")

        # Start uvicorn in daemon thread (non-blocking)
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
                config = uvicorn.Config(app, host=host, port=port, log_level="error", loop="asyncio", http="h11", ws="none")
                srv = LocalServer(config)
                import asyncio
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

    except Exception:
        _log(f"error_{traceback.format_exc()}")
        return {"status": "error"}
