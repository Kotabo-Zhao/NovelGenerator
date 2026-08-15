import asyncio
from unittest.mock import MagicMock


def _placeholder_client():
    client = MagicMock()
    client.api_key = "sk-placeholder-for-tests"
    return client


def _collect(stream):
    async def run():
        return [event async for event in stream]

    return asyncio.run(run())


def _base_state(novel_id="book"):
    from backend.core.interactive.interact_store import new_state

    state = new_state(novel_id, "测试小说", "悬疑", "冷峻", "林川")
    state["player_char"] = {"name": "林川", "identity": "记者"}
    state["player_state"] = {
        "location": "旧城区",
        "time": "夜晚",
        "with": ["苏禾"],
        "holding": [],
        "situation": "正在查案",
    }
    state["state"]["location"] = "旧城区"
    state["state"]["objective"] = "找到录音来源"
    state["casts"] = {
        "苏禾": {
            "present": True,
            "identity": "法医",
            "profile": {"expression_dna": [], "anti_patterns": []},
        }
    }
    state["node_chars"] = ["苏禾"]
    return state


def test_scene_provider_failure_does_not_advance_or_write_filler(tmp_path):
    from backend.core.interactive.interact_store import InteractStore
    from backend.core.interactive.story_director import StoryDirector

    store = InteractStore(str(tmp_path))
    store.save_state("book", _base_state())
    director = StoryDirector(_placeholder_client(), "test-model", store)

    events = _collect(director.generate_scene_stream("book"))
    state = store.load_state("book")

    assert any(event.get("type") == "error" for event in events)
    assert any("DEEPSEEK_API_KEY" in event.get("message", "") for event in events)
    assert state["scene_num"] == 0
    assert store.recent_scenes("book", 10) == []
    assert state.get("recent_blocks") in (None, [])


def test_dialogue_provider_failure_keeps_user_input_but_writes_no_fake_reply(tmp_path):
    from backend.core.interactive.dialogue_engine import DialogueEngine
    from backend.core.interactive.interact_store import InteractStore

    store = InteractStore(str(tmp_path))
    store.save_state("book", _base_state())
    dialogue = DialogueEngine(_placeholder_client(), "test-model", store)

    events = _collect(dialogue.chat_stream("book", "录音里还有别的声音吗？", "苏禾", force_talk=True))
    chats = store.recent_chats("book", 10)

    assert any(event.get("type") == "error" for event in events)
    assert any("DEEPSEEK_API_KEY" in event.get("message", "") for event in events)
    assert [chat["role"] for chat in chats] == ["user"]
    assert all("沉默片刻" not in chat.get("content", "") for chat in chats)


def test_action_result_provider_failure_writes_no_generic_narration(tmp_path):
    from backend.core.interactive.action_engine import ActionEngine
    from backend.core.interactive.interact_store import InteractStore

    store = InteractStore(str(tmp_path))
    store.save_state("book", _base_state())
    action = ActionEngine(_placeholder_client(), "test-model", store)

    events = _collect(
        action.action_scene_stream(
            "book",
            {"type": "act", "summary": "检查窗台上的泥点", "target": "窗台"},
            ["发现新线索"],
        )
    )

    assert any(event.get("type") == "error" for event in events)
    assert any("DEEPSEEK_API_KEY" in event.get("message", "") for event in events)
    assert store.recent_chats("book", 10) == []
    assert store.load_state("book").get("recent_blocks") in (None, [])


def test_detected_action_failure_does_not_emit_action_done_or_auto_advance_signal(tmp_path):
    from backend.core.interactive.dialogue_engine import DialogueEngine
    from backend.core.interactive.interact_store import InteractStore

    store = InteractStore(str(tmp_path))
    store.save_state("book", _base_state())
    dialogue = DialogueEngine(_placeholder_client(), "test-model", store)

    events = _collect(dialogue.chat_stream("book", "我推开房门", "苏禾"))
    event_types = [event.get("type") for event in events]

    assert "error" in event_types
    assert "action_done" not in event_types
    error = next(event for event in events if event.get("type") == "error")
    assert error.get("snapshot") is not None
