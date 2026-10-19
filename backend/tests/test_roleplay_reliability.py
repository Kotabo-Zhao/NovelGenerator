"""Regression coverage for protocol leakage, durability and overlapping requests."""
import asyncio
from types import SimpleNamespace
import time

import pytest

from core.llm_text import display_text, extract_text
from core.resilient_client import ResilientLLMClient, RetryConfig
from core.roleplay.engine import RoleplayEngine
from core.roleplay.store import RoleplayStore


def run(coro):
    return asyncio.run(coro)


async def collect(engine, **kwargs):
    return [event async for event in engine.stream_reply("builtin-suwan", **kwargs)]


class Stream:
    def __init__(self, parts, failure=False):
        self.parts, self.failure, self.calls = parts, failure, 0

    async def create_stream(self, **kwargs):
        self.calls += 1
        for part in self.parts:
            yield part
        if self.failure:
            raise ConnectionError("offline")


@pytest.fixture
def engine(tmp_path):
    return RoleplayEngine(object(), "test", RoleplayStore(str(tmp_path / "rp")))


@pytest.mark.parametrize("value,expected", [
    ({"choices": [{"delta": {"content": "你好"}}]}, "你好"),
    ({"choices": [{"delta": {"reasoning_content": "internal"}}]}, ""),
    ([{"type": "text", "text": "甲"}, {"type": "image_url", "image_url": "secret"}, {"type": "text", "text": "乙"}], "甲乙"),
    ({"choices": []}, ""),
    ({"usage": {"total_tokens": 1}}, ""),
])
def test_text_extraction_never_displays_protocol(value, expected):
    assert extract_text(value) == expected


def test_old_serialized_provider_message_is_readable():
    assert display_text('{"choices":[{"delta":{"content":"你好\\n世界"}}]}') == "你好\n世界"
    assert display_text("[说明] {普通小说里的括号}") == "[说明] {普通小说里的括号}"


def test_unknown_object_is_rejected_instead_of_stringified():
    with pytest.raises(TypeError):
        extract_text(object())


def test_structured_chunks_are_text_in_stream_and_on_disk(engine):
    engine._resilient = Stream([{"content": [{"type": "text", "text": "你好"}]}, {"choices": []}])
    events = run(collect(engine, message="在吗"))
    assert [e["content"] for e in events if e["type"] == "delta"] == ["你好"]
    assert events[-1]["message"]["content"] == "你好"
    assert engine.store.chat_messages("builtin-suwan")[-1]["content"] == "你好"


def test_partial_failure_saved_once(engine):
    engine._resilient = Stream(["已经收到的中文"], failure=True)
    events = run(collect(engine, message="开始"))
    assert events[-1]["type"] == "error"
    transcript = engine.store.chat_messages("builtin-suwan")
    assert transcript[-1]["content"] == "已经收到的中文"
    assert transcript[-1]["partial"] is True
    assert not engine.store.is_generating("builtin-suwan")


def test_failed_regeneration_does_not_destroy_old_reply(engine):
    engine._resilient = Stream(["原回复"])
    run(collect(engine, message="问题"))
    engine._resilient = Stream([], failure=True)
    run(collect(engine, regenerate=True))
    assert engine.store.chat_messages("builtin-suwan")[-1]["content"] == "原回复"


def test_replayed_request_is_idempotent(engine):
    engine._resilient = Stream(["一次回复"])
    run(collect(engine, message="问题", request_id="stable-request"))
    duplicate = run(collect(engine, message="问题", request_id="stable-request"))
    assert duplicate[-1]["duplicate"] is True
    assert engine._resilient.calls == 1
    assert len([m for m in engine.store.chat_messages("builtin-suwan") if m["role"] == "user"]) == 1


def test_cancelling_consumer_persists_partial_and_releases_guard(engine):
    started = asyncio.Event()

    class Slow:
        async def create_stream(self, **kwargs):
            yield "保留这段"
            started.set()
            await asyncio.sleep(60)

    async def scenario():
        engine._resilient = Slow()
        task = asyncio.create_task(collect(engine, message="测试停止"))
        await started.wait()
        overlap = await collect(engine, message="不能写入")
        assert overlap == [{"type": "error", "code": "busy", "message": "这个角色正在回复，请等待或停止当前生成"}]
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(scenario())
    assert engine.store.chat_messages("builtin-suwan")[-1]["content"] == "保留这段"
    assert not engine.store.is_generating("builtin-suwan")
    assert "不能写入" not in str(engine.store.chat_messages("builtin-suwan"))


def test_async_generator_close_persists(engine):
    engine._resilient = Stream(["第一段", "第二段"])

    async def scenario():
        generator = engine.stream_reply("builtin-suwan", message="测试关闭")
        assert (await anext(generator))["type"] == "meta"
        assert (await anext(generator))["content"] == "第一段"
        await generator.aclose()

    run(scenario())
    assert engine.store.chat_messages("builtin-suwan")[-1]["content"] == "第一段"
    assert not engine.store.is_generating("builtin-suwan")


def test_clear_archives_and_cache_is_defensive(engine):
    engine._resilient = Stream(["旧的内容"])
    run(collect(engine, message="旧的问题"))
    engine.reset_chat("builtin-suwan")
    assert list((__import__('pathlib').Path(engine.store.root) / 'history' / 'builtin-suwan').glob('*.json'))
    cards = engine.store.list_characters()
    cards[0]["name"] = "mutated"
    assert engine.store.get_character(cards[0]["id"])["name"] != "mutated"


@pytest.mark.parametrize("identifier", ["../outside", "..", "/absolute", "C:\\temp", "a/b", "", "a\x00b"])
def test_storage_paths_are_confined(engine, identifier):
    with pytest.raises(ValueError):
        engine.store.chat_path(identifier)


def test_sync_sdk_iteration_does_not_block_event_loop():
    ticks = []

    class Response:
        closed = False
        def __iter__(self):
            for _ in range(3):
                time.sleep(.04)
                yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="文"))])
        def close(self):
            self.closed = True

    response = Response()
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: response)))
    gateway = ResilientLLMClient(client, "test", RetryConfig(max_retries=0))

    async def ticker():
        for _ in range(8):
            await asyncio.sleep(.012)
            ticks.append(time.monotonic())

    async def scenario():
        task = asyncio.create_task(ticker())
        result = [part async for part in gateway.create_stream(messages=[])]
        concurrent_ticks = len(ticks)
        await task
        return result, concurrent_ticks

    result, count = run(scenario())
    assert result == ["文"] * 3
    assert count >= 5
    assert response.closed
