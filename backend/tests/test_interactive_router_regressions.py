"""互动 API 路由的真实调用级回归测试。"""

import asyncio


def test_end_chat_enqueues_player_state_update(tmp_path, monkeypatch):
    """end-chat 应把对话后的主角状态提取交给串行后台队列。"""
    from api.routers import interactive as router
    from core.interactive.interact_store import InteractStore, new_state

    store = InteractStore(str(tmp_path))
    store.save_state("book", new_state("book", "测试小说", "悬疑", "克制现实", "林川"))
    store.append_chat("book", {"role": "user", "content": "我答应今晚去旧仓库。"})

    class StubStory:
        def extract_pact(self, _novel_id, _chat_entries):
            return {"tone": "紧张"}

        def _extract_player_state(self, _novel_id, _transcript):
            raise AssertionError("状态提取应被入队，而不是在请求线程内执行")

    queued = []
    monkeypatch.setattr(router, "_store", store)
    monkeypatch.setattr(router, "_story", StubStory())
    monkeypatch.setattr(
        router,
        "enqueue_background",
        lambda *args, **kwargs: queued.append((args, kwargs)),
    )

    result = asyncio.run(router.interactive_end_chat("book"))

    assert result["ok"] is True
    assert len(queued) == 1
    args, kwargs = queued[0]
    assert args[0].__name__ == "_extract_player_state"
    assert args[0].__self__.__class__ is StubStory
    assert args[1] == "book"
    assert "本轮对话" in args[2]
    assert kwargs == {"critical": False, "novel_id": "book"}
