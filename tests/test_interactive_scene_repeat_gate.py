import asyncio
from pathlib import Path
from unittest.mock import MagicMock

from backend.core.interactive.interact_store import InteractStore, new_state
from backend.core.interactive.story_director import StoryDirector


OLD_SCENE = "[旁白]林川推开旧仓库的铁门，灰尘落在录音机上。\n[苏禾]别碰，外壳上可能有指纹。"
NEW_SCENE = "[旁白]二楼突然断电，林川循着急促脚步追进消防通道。\n[苏禾]楼梯口有人，把出口守住。"


def _collect(stream):
    async def _run():
        return [event async for event in stream]

    return asyncio.run(_run())


def _director_with_history(tmp_path, responses):
    store = InteractStore(str(tmp_path))
    state = new_state("book", "测试小说", "悬疑", "冷峻", "林川")
    state["player_char"] = {"name": "林川", "identity": "记者"}
    state["player_state"] = {
        "location": "旧仓库", "time": "夜晚", "with": ["苏禾"],
        "holding": [], "situation": "追查录音来源",
    }
    state["state"]["location"] = "旧仓库"
    state["state"]["objective"] = "找到录音来源"
    state["scene_num"] = 1
    store.save_state("book", state)
    store.append_scene("book", {
        "scene_num": 1,
        "scene_text": OLD_SCENE,
        "blocks": [{"type": "narration", "speaker": "", "content": OLD_SCENE}],
        "ts": "2026-08-20 00:00:00",
    })

    client = MagicMock()
    director = StoryDirector(client, "test-model", store)
    queue = list(responses)

    async def _fake_stream(*_args, **_kwargs):
        text = queue.pop(0)
        yield text

    director._llm_stream = _fake_stream
    return director, store


def test_duplicate_interactive_scene_is_retried_before_persistence(tmp_path, monkeypatch):
    director, store = _director_with_history(tmp_path, [OLD_SCENE, NEW_SCENE])
    monkeypatch.setattr(
        "backend.core.interactive.story_director.enqueue_background",
        lambda *_args, **_kwargs: None,
    )

    events = _collect(director.generate_scene_stream("book", force_node_check=False))
    scenes = store.recent_scenes("book", 10)

    assert [event["type"] for event in events].count("scene_retry") == 1
    assert [event["type"] for event in events].count("scene_end") == 1
    assert len(scenes) == 2
    assert scenes[-1]["scene_text"] == NEW_SCENE
    assert store.load_state("book")["scene_num"] == 2


def test_second_duplicate_is_not_written_or_counted_as_a_scene(tmp_path, monkeypatch):
    director, store = _director_with_history(tmp_path, [OLD_SCENE, OLD_SCENE])
    monkeypatch.setattr(
        "backend.core.interactive.story_director.enqueue_background",
        lambda *_args, **_kwargs: None,
    )

    events = _collect(director.generate_scene_stream("book", force_node_check=False))

    assert any(event.get("type") == "error" and "重复" in event.get("message", "") for event in events)
    assert not any(event.get("type") == "scene_end" for event in events)
    assert len(store.recent_scenes("book", 10)) == 1
    assert store.load_state("book")["scene_num"] == 1


def test_frontend_discards_streamed_duplicate_before_retrying():
    source = (Path(__file__).parents[1] / "web" / "index.html").read_text(encoding="utf-8")

    assert source.count("evt.type==='scene_retry'") >= 2
    assert "_dropTypingDraft()" in source
