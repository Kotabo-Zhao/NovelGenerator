import asyncio
import json
from unittest.mock import MagicMock

from api.middleware import NovelWorldJobs
from api.jobs import is_writing
from core.interactive.interact_store import InteractStore, new_state
from core.interactive.story_director import StoryDirector


def test_save_and_read_never_share_mutable_cache(tmp_path):
    store = InteractStore(str(tmp_path))
    state = new_state('book', '原作', '', '')
    store.save_state('book', state)
    state['state']['inventory'].append('未保存物品')
    loaded = store.load_state('book')
    assert loaded['state']['inventory'] == []
    loaded['state']['location'] = '未保存地点'
    assert store.load_state('book')['state']['location'] == ''
    updated = store.update_state('book', lambda st: {**st, 'scene_num': 2})
    updated['state']['inventory'].append('外部改动')
    assert store.load_state('book')['state']['inventory'] == []
    assert store.load_state('book')['revision'] == 2
    assert json.loads((tmp_path / 'book/interactive/state.json').read_text(encoding='utf-8'))['revision'] == 2


def test_late_model_extraction_cannot_overwrite_newer_progress(tmp_path):
    store = InteractStore(str(tmp_path))
    state = new_state('book', '原作', '', '')
    state['player_state'] = {'location': '客栈'}
    store.save_state('book', state)
    director = StoryDirector(MagicMock(), 'model', store)
    def slow_model(*args, **kwargs):
        store.update_state('book', lambda st: {**st, 'scene_num': 3, 'player_state': {'location': '城门'}})
        return json.dumps({'player': {'location': '客栈'}})
    director._llm = slow_model
    director._extract_player_state('book', '旧场景')
    assert store.load_state('book')['player_state']['location'] == '城门'
    assert store.load_state('book')['scene_num'] == 3


def test_light_asset_sync_does_not_read_chapter_bodies(tmp_path, monkeypatch):
    from core.interactive import novel_assets
    (tmp_path / 'book').mkdir()
    (tmp_path / 'book/plan.json').write_text('{"title":"book"}', encoding='utf-8')
    monkeypatch.setattr(novel_assets, 'NOVELS_DIR', str(tmp_path))
    monkeypatch.setattr(novel_assets, '_roleplay_cards', lambda *_args: {})
    def forbidden(*args):
        raise AssertionError('Entry must not read every chapter')
    monkeypatch.setattr(novel_assets, '_chapter_files', forbidden)
    assert novel_assets.load_novel_assets('book', include_chapters=False)['chapters'] == []


def test_queued_scene_rules_cannot_advance_a_newer_or_restarted_world(tmp_path):
    store = InteractStore(str(tmp_path))
    state = new_state('book', '原作', '', '')
    state['scene_num'] = 3
    store.save_state('book', state)
    director = StoryDirector(MagicMock(), 'model', store)
    director._extract_player_state = lambda *args: None
    before = store.load_state('book')
    director._post_scene_logic_check('book', 1, '夜深了。', [])
    assert store.load_state('book') == before
    director._post_scene_logic_check('book', 3, '风吹过窗户。', [])
    after = store.load_state('book')
    director._post_scene_logic_check('book', 3, '风吹过窗户。', [])
    assert store.load_state('book') == after
    assert store.restart('book')
    reset = store.load_state('book')
    director._post_scene_logic_check('book', 3, '迟到的后台任务。', [])
    assert store.load_state('book') == reset


def test_novel_job_lock_covers_stream_and_cancellation():
    async def scenario():
        started = asyncio.Event()
        release = asyncio.Event()
        async def downstream(scope, receive, send):
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
            if scope['path'].endswith('/start'):
                started.set()
                await release.wait()
            await send({'type': 'http.response.body', 'body': b'done'})
        middleware = NovelWorldJobs(downstream)
        async def receive():
            return {'type': 'http.request', 'body': b''}
        messages = []
        async def send(message):
            messages.append(message)
        scope = {'type': 'http', 'method': 'POST', 'path': '/api/novels/book/interactive/start'}
        first = asyncio.create_task(middleware(scope, receive, send))
        await started.wait()
        assert is_writing('book')
        await middleware({**scope, 'path': '/api/novels/book/interactive/chat'}, receive, send)
        assert messages[-2]['status'] == 409
        await middleware({**scope, 'path': '/api/novels/other/interactive/chat'}, receive, send)
        assert messages[-2]['status'] == 200
        first.cancel()
        try:
            await first
        except asyncio.CancelledError:
            pass
        assert not is_writing('book')
        await middleware({**scope, 'path': '/api/novels/book/interactive/chat'}, receive, send)
        assert messages[-2]['status'] == 200
    asyncio.run(scenario())


def test_missing_novel_is_rejected_before_creating_save(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.routers import interactive
    monkeypatch.setattr(interactive.engine, 'get_novel', lambda novel_id: None)
    store = InteractStore(str(tmp_path))
    monkeypatch.setattr(interactive, '_store', store)
    app = FastAPI()
    app.include_router(interactive.router)
    assert TestClient(app).post('/api/novels/missing/interactive/start', json={}).status_code == 404
    assert not (tmp_path / 'missing').exists()
