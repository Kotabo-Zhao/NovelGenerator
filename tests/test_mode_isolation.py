import json
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace


def test_storygraph_is_removed_from_normal_memory_and_api():
    from backend.core.shared_memory import MEMORY_FILES

    assert "storygraph" not in MEMORY_FILES

    source = Path(__file__).parents[1] / "backend" / "api" / "server.py"
    assert "storygraph" not in source.read_text(encoding="utf-8").lower()

    for frontend in (
        Path(__file__).parents[1] / "web" / "index.html",
        Path(__file__).parents[1] / "backend" / "web" / "index.html",
        Path(__file__).parents[1] / "android" / "app" / "src" / "main" / "python" / "web" / "index.html",
    ):
        content = frontend.read_text(encoding="utf-8").lower()
        assert "storygraph" not in content
        assert "@click=\"openinteractive\"" in content


def test_interactive_resume_restores_controls_and_actions():
    frontend = Path(__file__).parents[1] / "web" / "index.html"
    content = frontend.read_text(encoding="utf-8")

    assert "interactive.pendingNode = !!data.state.pending_node;" in content
    assert "interactive.pendingReason = data.state.pending_reason || '';" in content
    assert "interactive.suggestions = (data.state.suggestions || []).filter" in content
    assert "v-if=\"interactive.actions.length && !interactive.busy && !interactive.typing\"" in content


def test_interactive_new_game_uses_role_picker_and_selected_character():
    frontend = Path(__file__).parents[1] / "web" / "index.html"
    content = frontend.read_text(encoding="utf-8")

    assert "/interactive/cast-options" in content
    assert "interactive.rolePicker" in content
    assert "interactive.selectedRole" in content
    assert "JSON.stringify({character: interactive.selectedRole || null})" in content
    assert "Number(data.state.scene_num || 0) > 0" in content


def test_interactive_controls_are_grouped_and_node_button_enters_chat_first():
    frontend = Path(__file__).parents[1] / "web" / "index.html"
    content = frontend.read_text(encoding="utf-8")

    assert "interactive.settingsOpen=!interactive.settingsOpen" in content
    assert 'v-if="interactive.settingsOpen"' in content
    assert "if (interactive.pendingNode && !interactive.inChat)" in content
    assert "if (interactive.inChat) {" in content
    assert "function _dropTypingPlaceholder()" in content


def test_interactive_start_persists_selected_role_before_stream(monkeypatch, tmp_path):
    backend_root = Path(__file__).parents[1] / "backend"
    monkeypatch.syspath_prepend(str(backend_root))
    from api.routers import interactive as interactive_router
    from core.interactive.interact_store import InteractStore

    class FakeStory:
        def build_context_from_bible(self, novel_id):
            return {
                "title": "测试小说",
                "genre": "悬疑",
                "style": "冷峻",
                "protagonist_name": "主角",
                "protagonist": {"name": "主角", "identity": "记者"},
                "worldbuilding": {"geography": "旧城区", "core_conflict": "查明真相"},
            }

        def _load_plan(self, novel_id):
            return {
                "characters": {
                    "protagonist": {"name": "主角", "identity": "记者"},
                    "supporting": [{"name": "搭档", "identity": "法医"}],
                }
            }

        def enrich_location_descs(self, novel_id, state):
            return None

    store = InteractStore(str(tmp_path))
    monkeypatch.setattr(interactive_router, "_store", store)
    monkeypatch.setattr(interactive_router, "_story", FakeStory())

    response = asyncio.run(
        interactive_router.interactive_start(
            "book", interactive_router.StartRequest(character="搭档")
        )
    )

    state = store.load_state("book")
    assert response.media_type == "text/event-stream"
    assert state is not None
    assert state["mode"] == "interactive"
    assert state["player_char"]["name"] == "搭档"
    assert state["cast_choices"]["char"] == "搭档"
    assert "冷峻" in state["style_brief"]
    assert "莫欺少年穷" not in state["style_brief"]


def test_interactive_streams_do_not_persist_generic_failure_filler():
    root = Path(__file__).parents[1] / "backend" / "core" / "interactive"
    story = (root / "story_director.py").read_text(encoding="utf-8")
    dialogue = (root / "dialogue_engine.py").read_text(encoding="utf-8")
    action = (root / "action_engine.py").read_text(encoding="utf-8")

    assert "夜色渐深，{state.get('title', '故事')}还在继续" not in story
    assert "（沉默片刻）……你说什么？" not in dialogue
    assert "你做了这个决定，{state.get('title', '故事')}的走向因此改变" not in action


def test_interactive_state_is_marked_as_a_private_line(tmp_path):
    from backend.core.interactive.interact_store import InteractStore, new_state

    store = InteractStore(str(tmp_path))
    state = new_state("book", "Book", "mystery", "cool")
    store.save_state("book", state)

    assert state["mode"] == "interactive"
    assert (tmp_path / "book" / "interactive" / "state.json").exists()
    assert not (tmp_path / "book" / "global_state.json").exists()
    assert not (tmp_path / "book" / "chapters").exists()

    legacy = tmp_path / "legacy" / "interactive"
    legacy.mkdir(parents=True)
    (legacy / "state.json").write_text(json.dumps({"state": {}, "scene_num": 1}), encoding="utf-8")
    migrated = InteractStore(str(tmp_path)).load_state("legacy")
    assert migrated["mode"] == "interactive"
    assert migrated["line_id"] == "interactive:legacy"


def test_normal_writer_context_ignores_removed_storygraph_file(tmp_path):
    from backend.core.shared_memory import SharedMemoryManager

    novel_dir = tmp_path / "book"
    novel_dir.mkdir()
    (novel_dir / "storygraph.json").write_text(
        json.dumps({"marker": "formal-storygraph-must-not-enter-context"}, ensure_ascii=False),
        encoding="utf-8",
    )

    memory = SharedMemoryManager(str(tmp_path))
    context = memory.build_context(
        "writer",
        "book",
        chapter_num=1,
        chapter_outline={"summary": "opening"},
    )

    assert "formal-storygraph-must-not-enter-context" not in context


def test_interactive_scene_prompt_ignores_formal_line_fields():
    from backend.core.interactive.story_director import StoryDirector

    director = StoryDirector.__new__(StoryDirector)
    director.store = SimpleNamespace(recent_chats=lambda *args: [])
    state = {
        "novel_id": "book",
        "title": "Book",
        "genre": "mystery",
        "style": "cool",
        "scene_num": 1,
        "state": {"location": "tea-house"},
        "timeline_brief": "formal-timeline-must-not-enter-interactive",
        "foreshadows_brief": "formal-foreshadowing-must-not-enter-interactive",
        "outline_chapters": [],
    }

    prompt = director._build_scene_prompt(state, "interactive-line-own-summary")

    assert "interactive-line-own-summary" in prompt
    assert "formal-timeline-must-not-enter-interactive" not in prompt
    assert "formal-foreshadowing-must-not-enter-interactive" not in prompt


def test_interactive_atomic_writer_handles_concurrent_requests(tmp_path):
    from backend.core.interactive.interact_store import _atomic_write_json

    path = str(tmp_path / "interactive" / "state.json")

    def write(index):
        _atomic_write_json(path, {"mode": "interactive", "writer": index})

    with ThreadPoolExecutor(max_workers=16) as pool:
        errors = [error for error in pool.map(lambda i: _capture(write, i), range(64)) if error]

    assert errors == []
    assert json.loads(Path(path).read_text(encoding="utf-8"))["mode"] == "interactive"


def test_interactive_store_cleans_legacy_fake_locations_on_load(tmp_path):
    from backend.core.interactive.interact_store import InteractStore

    store = InteractStore(str(tmp_path))
    state_dir = tmp_path / "book" / "interactive"
    state_dir.mkdir(parents=True)
    (state_dir / "state.json").write_text(
        json.dumps(
            {
                "state": {"location": "茶楼"},
                "player_state": {"location": "茶楼"},
                "world": {
                    "location": "茶楼",
                    "time": {"label": "正午", "slot": 2, "day": 1},
                    "chars": {},
                    "locations": {
                        "茶楼": {"connected": ["王朝名为大朔", "城西码头"]},
                        "王朝名为大朔": {"connected": ["茶楼"]},
                        "城西码头": {"connected": ["茶楼"]},
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    loaded = store.load_state("book")

    assert "王朝名为大朔" not in loaded["world"]["locations"]
    assert loaded["world"]["locations"]["茶楼"]["connected"] == ["城西码头"]


def _capture(fn, value):
    try:
        fn(value)
    except Exception as exc:  # pragma: no cover - failure detail is asserted by caller
        return repr(exc)
    return ""
