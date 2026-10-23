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


def test_application_serves_canonical_assets_and_novel_world(monkeypatch, tmp_path):
    from api.server import app, WEB_DIR
    assert Path(WEB_DIR).name == "web"
    assert (Path(WEB_DIR) / "index.html").is_file()
    client = TestClient(app)
    for path in ("/", "/scene.html", "/assets/scene.js", "/assets/scene.css", "/roleplay.html", "/assets/runtime.js", "/assets/theme.css", "/assets/workshop.js"):
        assert client.get(path).status_code == 200
    assert any(
        getattr(route, "path", None) == "/api/novels/{novel_id}/interactive/start"
        for route in app.routes
    )
    assert client.get("/api/system/connection").status_code == 200
    assert "api_key" not in client.get("/api/system/connection").json()
    response = client.get("/api/novels/C:secret")
    assert response.status_code == 401


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
    backups = list(tmp_path.glob('state.json.corrupted.*'))
    assert len(backups) == 1
    assert backups[0].read_text(encoding='utf-8')=='{broken'


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


@pytest.mark.parametrize('kind',['characters','chat','worldinfo','settings','identity'])
def test_corrupt_roleplay_data_cannot_be_overwritten_by_followup_save(tmp_path,kind):
    from core.roleplay.store import RoleplayStore
    from core.atomic_io import StoredDataError
    store=RoleplayStore(str(tmp_path/'cards'));cid='builtin-suwan'
    paths={'characters':store.characters_file,'chat':store.chat_path(cid),'worldinfo':store.worldinfo_path(cid),
           'settings':store.settings_file,'identity':store.identity_path(cid)}
    path=Path(paths[kind]);path.write_bytes(b'{broken')
    actions={'characters':lambda:store.upsert_character({'id':'new','name':'New'}),
             'chat':lambda:store.save_chat(cid,[]),'worldinfo':lambda:store.save_worldinfo(cid,[]),
             'settings':lambda:store.update_settings({'user_name':'New'}),
             'identity':lambda:store.update_identity(cid,{'user_name':'New'})}
    with pytest.raises(StoredDataError):actions[kind]()
    assert path.read_bytes()==b'{broken'
    assert list(path.parent.glob(path.name+'.corrupted.*'))
    # An externally recovered file is usable without restarting the application.
    path.write_text('[]' if kind=='characters' else '{}',encoding='utf-8')
    actions[kind]()


def test_nonretryable_provider_error_is_preserved():
    import httpx
    from openai import APIStatusError
    from types import SimpleNamespace
    from core.resilient_client import ResilientLLMClient
    response = httpx.Response(400, request=httpx.Request("POST", "https://example.invalid"))
    original = APIStatusError("invalid request", response=response, body=None)
    calls = []
    def fail(**kwargs):
        calls.append(kwargs)
        raise original
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fail)))
    with pytest.raises(APIStatusError) as caught:
        ResilientLLMClient(client, "fixture").create(messages=[])
    assert caught.value is original
    assert len(calls) == 1


def test_dialogue_prompt_uses_current_novel_scene_and_rounds():
    from core.interactive.dialogue_engine import DialogueEngine, CHAT_WARN_ROUNDS
    from types import SimpleNamespace
    calls = []
    def scenes(novel_id, limit):
        calls.append(novel_id)
        return [{"blocks": [{"speaker": "守卫", "content": "城门刚刚关闭。"}]}]
    store = SimpleNamespace(recent_scenes=scenes, recent_chats=lambda nid, limit: [{}] * (CHAT_WARN_ROUNDS * 2))
    engine = DialogueEngine(None, "fixture", store)
    prompt = engine._build_chat_prompt({"state": {"location": "城门"}}, "守卫", [], novel_id="fixture-novel")
    assert calls == ["fixture-novel"]
    assert "城门刚刚关闭。" in prompt
    assert "请让对话自然告一段落" in prompt
