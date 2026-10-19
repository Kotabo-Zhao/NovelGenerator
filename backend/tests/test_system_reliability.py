import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.middleware import RequestLimits
from core.paths import novel_path, InvalidNovelPathError


def test_generation_limit_is_held_for_entire_stream():
    started = asyncio.Event()
    release = asyncio.Event()

    async def downstream(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        started.set()
        await release.wait()
        await send({"type": "http.response.body", "body": b"done"})

    limiter = RequestLimits(downstream)
    limiter.concurrency = 1
    scope = {"type": "http", "path": "/api/roleplay/characters/a/chat/send", "method": "POST", "client": ("test", 1)}

    async def receive():
        return {"type": "http.request", "body": b""}

    async def scenario():
        messages = []
        async def send(message):
            messages.append(message)
        first = asyncio.create_task(limiter(scope, receive, send))
        await started.wait()
        assert limiter.active["test"] == 1
        await limiter(scope, receive, send)
        assert any(m.get("status") == 429 for m in messages)
        release.set()
        await first
        assert "test" not in limiter.active

    asyncio.run(scenario())


@pytest.mark.parametrize("name", ["..", ".", "../secret", "a\\b", "C:folder", "tail.", "name\x00", "name\n"])
def test_novel_path_rejects_unsafe_identifiers(tmp_path, name):
    with pytest.raises(InvalidNovelPathError):
        novel_path(str(tmp_path), name)


def test_application_serves_canonical_assets_and_no_legacy_mode(monkeypatch, tmp_path):
    from api.server import app, WEB_DIR
    assert Path(WEB_DIR).name == "web"
    assert Path(WEB_DIR).parent.name == "NovelGenerator"
    client = TestClient(app)
    for path in ("/", "/roleplay.html", "/assets/runtime.js", "/assets/theme.css", "/assets/workshop.js"):
        assert client.get(path).status_code == 200
    assert client.post("/api/novels/example/interactive/start").status_code == 404
    assert client.get("/api/system/connection").status_code == 200
    assert "api_key" not in client.get("/api/system/connection").json()
    response = client.get("/api/novels/C:secret")
    assert response.status_code == 400


def test_second_novel_job_is_rejected_and_cancel_releases_the_first():
    from api.jobs import stream_job, is_writing

    async def source():
        yield {"type": "progress"}
        await asyncio.sleep(60)

    async def scenario():
        first = stream_job("same-novel", source())
        assert (await anext(first))["type"] == "progress"
        assert is_writing("same-novel")
        second = [event async for event in stream_job("same-novel", source())]
        assert second[0]["type"] == "error"
        await first.aclose()
        assert not is_writing("same-novel")

    asyncio.run(scenario())


def test_corrupt_read_keeps_original_and_recovery_copy(tmp_path):
    from core.atomic_io import safe_read_json
    path=tmp_path/'state.json';path.write_text('{broken',encoding='utf-8')
    assert safe_read_json(str(path),default={})=={}
    assert path.read_text(encoding='utf-8')=='{broken'
    assert Path(str(path)+'.corrupted').read_text(encoding='utf-8')=='{broken'


def test_windows_transient_replace_failure_is_retried(tmp_path,monkeypatch):
    import core.atomic_io as module
    actual=module.os.replace
    calls=[]
    def transient(source,destination):
        calls.append(1)
        if len(calls)<3:
            raise PermissionError('temporarily busy')
        actual(source,destination)
    monkeypatch.setattr(module.os,'replace',transient)
    monkeypatch.setattr(module.time,'sleep',lambda _:None)
    path=tmp_path/'state.json'
    module.atomic_write_json(str(path),{'content':'中文完整写入'})
    assert module.safe_read_json(str(path))=={'content':'中文完整写入'}
    assert len(calls)==3


def test_recreating_workspace_preserves_existing_novel(tmp_path):
    from core.shared_memory import SharedMemoryManager
    memory=SharedMemoryManager(str(tmp_path))
    folder=Path(memory.create_novel_workspace('原作品'))
    marker=folder/'plan.json';marker.write_text('{"title":"原作品"}',encoding='utf-8')
    with pytest.raises(FileExistsError):
        memory.create_novel_workspace('原作品')
    assert marker.read_text(encoding='utf-8')=='{"title":"原作品"}'
