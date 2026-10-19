"""Tests for the SillyTavern-style roleplay mode.

Run from the backend directory:
    .venv/Scripts/python -m pytest tests/test_roleplay.py -q
"""
import asyncio
import base64
import json
import os
import struct
import sys
import tempfile
import zlib

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# Keep module-level store instances out of the repository.
os.environ.setdefault("ROLEPLAY_DIR", tempfile.mkdtemp(prefix="roleplay_import_"))

from core.roleplay import card_codec, prompt  # noqa: E402
from core.roleplay.engine import RoleplayEngine  # noqa: E402
from core.roleplay.novel_cards import build_cards_from_plan  # noqa: E402
from core.roleplay.store import RoleplayStore  # noqa: E402

CARD_V2 = {
    "spec": "chara_card_v2",
    "spec_version": "2.0",
    "data": {
        "name": "测试角色",
        "description": "一位{{char}}，安静而敏锐。",
        "personality": "温柔、喜欢调侃",
        "scenario": "深夜的书斋",
        "first_mes": "你好，{{user}}。",
        "mes_example": (
            "<START>\n{{user}}: 在吗？\n{{char}}: 在的，{{user}}。\n"
            "<START>\n{{user}}: 今天好累。\n{{char}}: 那就先坐一会儿。"
        ),
        "system_prompt": "",
        "post_history_instructions": "保持简短。",
        "alternate_greetings": ["晚上好。", "又见面了。"],
        "tags": ["测试", "治愈"],
        "creator": "tester",
        "character_version": "1.0",
    },
}


def _png_chunk(chunk_type: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + chunk_type + payload + struct.pack(">I", crc)


def make_png(entries, ihdr=True) -> bytes:
    """Build a minimal PNG carrying arbitrary tEXt entries {keyword: text}."""
    data = b"\x89PNG\r\n\x1a\n"
    if ihdr:
        data += _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
    for keyword, text in entries:
        data += _png_chunk(b"tEXt", keyword.encode("latin-1") + b"\x00" + text.encode("latin-1"))
    data += _png_chunk(b"IEND", b"")
    return data


def card_png(card: dict, keyword="chara") -> bytes:
    encoded = base64.b64encode(json.dumps(card, ensure_ascii=False).encode("utf-8")).decode("ascii")
    return make_png([(keyword, encoded)])


# --------------------------------------------------------------------- codec

def test_parse_png_card_v2():
    card, fmt = card_codec.parse_card_bytes(card_png(CARD_V2))
    assert fmt == "png"
    assert card["name"] == "测试角色"
    assert card["first_mes"] == "你好，{{user}}。"
    assert card["tags"] == ["测试", "治愈"]


def test_ccv3_takes_precedence_over_chara():
    v3 = {"spec": "chara_card_v3", "data": {"name": "V3角色", "description": "v3"}}
    png = make_png([
        ("chara", base64.b64encode(json.dumps(CARD_V2).encode()).decode()),
        ("ccv3", base64.b64encode(json.dumps(v3, ensure_ascii=False).encode()).decode()),
    ])
    card, fmt = card_codec.parse_card_bytes(png)
    assert fmt == "png"
    assert card["name"] == "V3角色"


def test_parse_plain_and_enveloped_json():
    plain = {"name": "裸卡", "description": "desc"}
    card, fmt = card_codec.parse_card_bytes(json.dumps(plain).encode())
    assert (fmt, card["name"]) == ("json", "裸卡")

    card, fmt = card_codec.parse_card_bytes(json.dumps(CARD_V2, ensure_ascii=False).encode())
    assert (fmt, card["name"]) == ("json", "测试角色")


def test_png_without_card_raises():
    png = make_png([("Comment", "just a picture")])
    with pytest.raises(card_codec.CardError):
        card_codec.parse_card_bytes(png)


def test_macros_and_greeting():
    assert card_codec.apply_macros("{{char}} 对 {{user}} 说", "苏晚", "阿明") == "苏晚 对 阿明 说"
    card = card_codec.normalize_card(CARD_V2)
    assert card_codec.pick_greeting(card) == "你好，{{user}}。"
    assert card_codec.pick_greeting(card, 2) == "又见面了。"


# -------------------------------------------------------------------- prompt

def test_system_prompt_contains_sections_and_macros():
    card = card_codec.normalize_card(CARD_V2)
    settings = {"user_name": "阿明", "user_persona": "一个失眠的旅人", "system_prompt": ""}
    system = prompt.build_system_prompt(card, settings)

    assert "一位测试角色" in system
    assert "[性格]" in system and "温柔" in system
    assert "[场景]" in system and "深夜的书斋" in system
    assert "[关于 阿明]" in system
    assert "[补充指示]" in system and "保持简短" in system


def test_example_dialogue_becomes_messages():
    card = card_codec.normalize_card(CARD_V2)
    messages = prompt.build_messages(
        card,
        {"user_name": "阿明", "system_prompt": "", "context_messages": 40},
        [{"role": "user", "content": "你好"}],
    )
    assert messages[0]["role"] == "system"
    roles = [m["role"] for m in messages[1:]]
    assert roles.count("assistant") >= 2
    assert any(m["content"] == "在的，阿明。" for m in messages)
    assert messages[-1] == {"role": "user", "content": "你好"}


def test_history_is_trimmed_to_context_window():
    card = card_codec.normalize_card(CARD_V2)
    history = [{"role": "assistant", "content": "开场"}] + [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"} for i in range(20)
    ]
    messages = prompt.build_messages(
        card, {"user_name": "阿明", "context_messages": 6}, history
    )
    contents = [m["content"] for m in messages]
    assert "m19" in contents
    assert "开场" not in contents


# --------------------------------------------------------------------- store

def test_store_crud_settings_and_chat(tmp_path):
    store = RoleplayStore(str(tmp_path / "rp"))
    cards = store.list_characters()
    assert len(cards) == 1 and cards[0]["id"] == "builtin-suwan"

    card = {
        "id": "abc", "name": "新角色", "description": "d", "personality": "p",
        "scenario": "s", "first_mes": "hi", "mes_example": "", "source": "manual",
    }
    store.upsert_character(card)
    assert store.get_character("abc")["name"] == "新角色"

    store.save_avatar("abc", b"\x89PNG\r\n\x1a\nfake")
    assert store.has_avatar("abc")
    assert store.avatar_media_type("abc") == "image/png"

    messages = [{"role": "assistant", "content": "hi", "ts": 1}]
    store.save_chat("abc", messages)
    assert store.chat_messages("abc")[0]["content"] == "hi"

    settings = store.update_settings({"temperature": 5, "max_tokens": 1, "user_name": "  "})
    assert settings["temperature"] == 2.0
    assert settings["max_tokens"] == 64
    assert settings["user_name"] == "旅行者"

    assert store.delete_character("abc") is True
    assert store.get_character("abc") is None
    assert not store.has_avatar("abc")


# --------------------------------------------------------------- novel cards

def test_novel_plan_to_cards(tmp_path):
    plan = {
        "title": "测试之书",
        "genre": "玄幻",
        "worldbuilding": {"era": "古代", "core_conflict": "人族与妖族之争"},
        "characters": {
            "protagonist": {
                "name": "顾尘", "identity": "落魄剑客", "appearance": "白衣",
                "backstory": "师门覆灭", "personality": {"surface": "冷淡", "true_self": "重情"},
                "motivation": {"want": "复仇", "need": "放下"},
            },
            "supporting": [{
                "name": "苏晚", "identity": "医女", "relation": "旧识",
                "personality": "温柔", "role": "治疗者", "meaning": "盟友",
            }],
            "antagonist": [{
                "name": "裴越", "motivation": "夺取剑冢", "power": "化神",
                "conflict": "理念冲突", "humanity": "护短",
            }],
        },
    }
    cards = build_cards_from_plan(plan, "novel-1")
    names = [c["name"] for c in cards]
    assert names == ["顾尘", "苏晚", "裴越"]
    assert cards[0]["source"] == "novel" and cards[0]["novel_id"] == "novel-1"
    assert "落魄剑客" in cards[0]["description"]
    assert "测试之书" in cards[0]["system_prompt"]


# -------------------------------------------------------------------- engine

class FakeResilient:
    def __init__(self, chunks=("你", "好")):
        self.chunks = chunks

    async def create_stream(self, messages, temperature=0.8, max_tokens=4096, **kwargs):
        for chunk in self.chunks:
            yield chunk


def _collect(engine, **kwargs):
    async def run():
        return [event async for event in engine.stream_reply(**kwargs)]

    return asyncio.run(run())


def test_engine_streams_and_persists(tmp_path):
    store = RoleplayStore(str(tmp_path / "rp"))
    engine = RoleplayEngine(object(), "fake-model", store)
    engine._resilient = FakeResilient(["你", "好"])

    events = _collect(engine, char_id="builtin-suwan", message="在吗")
    assert events[0]["type"] == "meta"
    assert [e.get("content") for e in events if e["type"] == "delta"] == ["你", "好"]
    assert events[-1]["type"] == "done"

    messages = store.chat_messages("builtin-suwan")
    assert messages[-2]["role"] == "user"
    assert messages[-1] == {**messages[-1], "role": "assistant", "content": "你好"}


def test_engine_regenerate_replaces_last_reply(tmp_path):
    store = RoleplayStore(str(tmp_path / "rp"))
    engine = RoleplayEngine(object(), "fake-model", store)
    engine.ensure_chat("builtin-suwan")  # greeting first, like the UI does
    engine._resilient = FakeResilient(["第一版"])
    _collect(engine, char_id="builtin-suwan", message="你好")
    engine._resilient = FakeResilient(["第二版"])
    _collect(engine, char_id="builtin-suwan", regenerate=True)
    messages = store.chat_messages("builtin-suwan")
    assert messages[-1]["content"] == "第二版"
    assert sum(1 for m in messages if m["role"] == "assistant") == 2  # greeting + new reply


# ----------------------------------------------------------------- API tests

class ApiFixture:
    def __init__(self, tmp_path, monkeypatch):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from api.routers import roleplay as rp

        self.rp = rp
        self.store = RoleplayStore(str(tmp_path / "rp_api"))
        self.engine = RoleplayEngine(object(), "fake-model", self.store)
        self.engine._resilient = FakeResilient(["流", "式"])
        monkeypatch.setattr(rp, "_store", self.store)
        monkeypatch.setattr(rp, "_engine", self.engine)

        app = FastAPI()
        app.include_router(rp.router)
        self.client = TestClient(app)


@pytest.fixture()
def api(tmp_path, monkeypatch):
    return ApiFixture(tmp_path, monkeypatch)


def test_api_characters_settings_and_import(api):
    client = api.client
    characters = client.get("/api/roleplay/characters").json()["characters"]
    assert any(c["id"] == "builtin-suwan" for c in characters)

    payload = base64.b64encode(json.dumps(CARD_V2, ensure_ascii=False).encode()).decode()
    response = client.post(
        "/api/roleplay/characters/import",
        json={"filename": "card.json", "data_base64": payload},
    )
    assert response.status_code == 200
    imported = response.json()["character"]
    assert imported["name"] == "测试角色" and imported["source"] == "json"

    response = client.put("/api/roleplay/settings", json={"temperature": 1.2, "user_name": "阿明"})
    assert response.json()["settings"]["temperature"] == 1.2
    assert response.json()["settings"]["user_name"] == "阿明"

    response = client.post("/api/roleplay/characters", json={"name": ""})
    assert response.status_code == 400


def test_api_chat_start_and_clear(api):
    client = api.client
    start = client.post("/api/roleplay/characters/builtin-suwan/chat/start").json()
    assert start["ok"] and len(start["messages"]) == 1
    msg = start["messages"][0]
    assert msg["role"] == "assistant" and "苏晚" in msg["content"]

    cleared = client.post("/api/roleplay/characters/builtin-suwan/chat/clear").json()
    assert cleared["messages"][0]["role"] == "assistant"


def test_api_send_streams_sse(api):
    client = api.client
    with client.stream(
        "POST",
        "/api/roleplay/characters/builtin-suwan/chat/send",
        json={"message": "你好"},
    ) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())

    events = [
        json.loads(line[len("data: "):])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]
    types = [event["type"] for event in events]
    assert "meta" in types and "delta" in types and "done" in types
    assert "".join(e.get("content", "") for e in events if e["type"] == "delta") == "流式"

    messages = api.store.chat_messages("builtin-suwan")
    assert messages[-2]["content"] == "你好"
    assert messages[-1]["content"] == "流式"


def test_api_from_novel(api, tmp_path, monkeypatch):
    novels_dir = tmp_path / "novels"
    novel_dir = novels_dir / "novel-1"
    novel_dir.mkdir(parents=True)
    (novel_dir / "plan.json").write_text(json.dumps({
        "title": "本书",
        "characters": {"protagonist": {"name": "顾尘", "identity": "剑客"}},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(api.rp, "NOVELS_DIR", str(novels_dir))

    response = api.client.post("/api/roleplay/characters/from-novel/novel-1")
    assert response.status_code == 200
    data = response.json()
    assert data["created"] == 1 and data["characters"][0]["name"] == "顾尘"

    again = api.client.post("/api/roleplay/characters/from-novel/novel-1").json()
    assert again["created"] == 0 and again["skipped"] == 1
