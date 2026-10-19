"""Check embedded Android Python sources on a host with the APK's pinned SDKs.

This is a host compatibility check, not an Android device run.
"""
from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
ANDROID = ROOT / "android/app/src/main/python"
REPORT = ROOT / "reports/android-4.0.0"


def port():
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        return connection.getsockname()[1]


def main():
    with tempfile.TemporaryDirectory(prefix="novelgen_android_verify_", ignore_cleanup_errors=True) as temporary:
        os.environ["NOVELGEN_NOVELS_DIR"] = str(Path(temporary) / "novels")
        os.environ["ROLEPLAY_DIR"] = str(Path(temporary) / "_roleplay")
        os.environ["DEEPSEEK_API_KEY"] = ""
        sys.path.insert(0, str(ANDROID))

        import config
        import httpx
        import uvicorn
        import server_runner

        assert Path(config.__file__).is_relative_to(ANDROID)
        expected = {"fastapi": "0.125.0", "pydantic": "1.10.26", "openai": "1.39.0", "httpx": "0.27.2"}
        versions = {name: importlib.metadata.version(name) for name in expected}
        assert versions == expected, versions
        spec = importlib.util.spec_from_file_location("android_verify_mock", ROOT / "backend/tests/mock_llm_server.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        mock_port, app_port = port(), port()
        mock = uvicorn.Server(uvicorn.Config(module.app, host="127.0.0.1", port=mock_port, log_level="error", ws="none"))
        mock_thread = threading.Thread(target=mock.run, daemon=True)
        mock_thread.start()
        for _ in range(100):
            if mock.started:
                break
            time.sleep(.05)
        assert mock.started

        checks = []
        result = server_runner.start_server("", "127.0.0.1", app_port, temporary)
        assert result["status"] == "done", Path(temporary, "novelgen_status.txt").read_text(encoding="utf-8")
        checks.append("server_runner starts without an API key and reports readiness after bind")
        with httpx.Client(base_url=f"http://127.0.0.1:{app_port}", trust_env=False, timeout=30) as client:
            for path in ("/", "/roleplay.html", "/assets/runtime.js", "/assets/theme.css", "/assets/roleplay.js", "/api/health"):
                response = client.get(path)
                assert response.status_code == 200, (path, response.status_code)
            import api.server
            assert Path(api.server.__file__).is_relative_to(ANDROID)
            assert Path(api.server.WEB_DIR).resolve() == ANDROID / "web"
            checks.append("embedded frontend, assets and health API are served from Android source")
            response = client.get("/api/system/connection").json()
            assert response["configured"] is False and "api_key" not in response
            response = client.put("/api/system/connection", json={"api_key": "sk-local-fixture", "base_url": f"http://127.0.0.1:{mock_port}/v1", "model": "mock-model"})
            assert response.status_code == 200, response.text
            assert client.post("/api/system/connection/test").json()["ok"]
            checks.append("Pydantic 1 provider configuration and real SDK HTTP probe work against local fixture")
            endpoint = "/api/roleplay/characters/builtin-suwan/chat"
            assert client.post(endpoint + "/start", json={}).status_code == 200
            response = client.post(endpoint + "/send", json={"message": "你好", "request_id": "android-compatibility"})
            events = [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]
            assert response.status_code == 200 and events[-1]["type"] == "done", events
            reply = "".join(event.get("content", "") for event in events if event["type"] == "delta")
            assert reply == module.REPLY
            assert "ChatCompletionChunk" not in response.text
            saved = client.get(endpoint).json()["messages"][-1]
            assert saved["content"] == reply
            checks.append("OpenAI 1.39 streams Chinese and emoji as plain text and saves the exact reply")
            assert client.post("/api/novels/example/interactive/start").status_code == 404
            checks.append("legacy interactive mode is absent")
            for fmt in ("pdf", "epub"):
                response = client.get(f"/api/novels/example/export?fmt={fmt}")
                assert response.status_code == 400 and "TXT" in response.json()["detail"]
            checks.append("Android PDF and EPUB are deferred; TXT remains enabled")
            assert server_runner.start_server("", "127.0.0.1", app_port, temporary)["status"] == "done"
            assert client.get("/api/health").status_code == 200
            checks.append("repeated service start reuses the running server")

        # The server is a daemon thread. Closing fixture connections releases all writes.
        mock.should_exit = True
        mock_thread.join(timeout=5)
        REPORT.mkdir(parents=True, exist_ok=True)
        report = {"scope": "Windows host, Android Python sources, pinned Android SDKs; no Android device",
                  "python": sys.version.split()[0], "dependencies": versions, "checks": checks,
                  "passed": len(checks), "real_provider": "NOT_RUN", "android_device": "NOT_RUN"}
        (REPORT / "backend-compatibility.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
