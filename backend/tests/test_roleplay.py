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
from core.roleplay import commands, novel_cards, worldinfo  # noqa: E402
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


class FakeLLMEngine:
    """Minimal stand-in for NovelEngine (multi-provider branch)."""

    def __init__(self):
        self.client = object()
        self.model = "fake-model"


class CapturingResilient(FakeResilient):
    def __init__(self, chunks=("好",)):
        super().__init__(chunks)
        self.messages = None

    async def create_stream(self, messages, temperature=0.8, max_tokens=4096, **kwargs):
        self.messages = messages
        async for chunk in super().create_stream(messages, temperature, max_tokens, **kwargs):
            yield chunk


def _collect(engine, **kwargs):
    async def run():
        return [event async for event in engine.stream_reply(**kwargs)]

    return asyncio.run(run())


def test_engine_streams_and_persists(tmp_path):
    store = RoleplayStore(str(tmp_path / "rp"))
    engine = RoleplayEngine(FakeLLMEngine(), store)
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
    engine = RoleplayEngine(FakeLLMEngine(), store)
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
        self.engine = RoleplayEngine(FakeLLMEngine(), self.store)
        self.engine._resilient = FakeResilient(["流", "式"])
        monkeypatch.setenv("NOVEL_GENERATOR_INTERNAL", "true")
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


# --------------------------------------------------- AI card export (from novel)

PLAN = {
    "title": "剑气长歌",
    "genre": "玄幻",
    "style": "热血",
    "worldbuilding": {"era": "古代", "core_conflict": "人族与妖族之争"},
    "characters": {
        "protagonist": {
            "name": "顾尘", "identity": "落魄剑客", "appearance": "白衣",
            "backstory": "师门覆灭", "personality": {"surface": "冷淡", "true_self": "重情"},
            "motivation": {"want": "复仇", "need": "放下"}, "secret": "剑冢传人",
        },
        "supporting": [{"name": "苏晚", "identity": "医女", "personality": "温柔"}],
    },
}

BIBLE = {
    "protagonist": {"name": "顾尘", "identity": "落魄剑客", "catchphrase": "剑在人在"},
    "supporting": [{"name": "苏晚", "identity": "医女", "role": "治疗者"}],
    "relationship_map": {"顾尘": ["苏晚：旧识"]},
}

PROFILES = {
    "顾尘": {
        "mental_models": ["世界是一场必须赢的棋局"],
        "decision_heuristics": ["当有人受伤时先拔剑"],
        "expression_dna": ["话少，句子短"],
        "anti_patterns": ["绝不向仇人低头"],
    }
}

VOICES = {"顾尘": {"catchphrases": ["剑在人在"], "address_style": "直呼其名"}}


class FakeAIClient:
    def __init__(self, cards):
        self.cards = cards

    def create(self, messages, temperature=0.8, max_tokens=4096, **kwargs):
        return json.dumps({"cards": self.cards}, ensure_ascii=False)


def test_ai_prompt_contains_bible_profile_and_voice():
    targets = novel_cards.plan_character_targets(PLAN)[:1]
    text = novel_cards.build_ai_prompt(PLAN, "n1", targets, BIBLE, PROFILES, VOICES)
    assert "顾尘" in text and "落魄剑客" in text
    assert "人设蒸馏卡" in text and "剑在人在" in text
    assert "声音卡" in text and "直呼其名" in text
    assert "人族与妖族之争" in text


def test_parse_ai_cards_handles_fences_and_list_shape():
    payload = json.dumps({"cards": [{"name": "顾尘", "first_mes": "AI开场"}]}, ensure_ascii=False)
    fenced = "```json\n" + payload + "\n```"
    assert novel_cards.parse_ai_cards(fenced)[0]["name"] == "顾尘"

    as_list = json.dumps([{"name": "苏晚", "first_mes": "你好"}], ensure_ascii=False)
    assert novel_cards.parse_ai_cards(as_list)[0]["name"] == "苏晚"
    assert novel_cards.parse_ai_cards("不是 JSON") == []


def test_generate_cards_with_ai_fills_st_fields():
    cards, warnings = asyncio.run(novel_cards.generate_cards_with_ai(
        FakeLLMEngine(), PLAN, "n1", BIBLE, PROFILES, VOICES, names=["顾尘"],
        resilient=FakeAIClient([{
            "name": "顾尘",
            "description": "AI 生成的外貌与背景",
            "personality": "AI 性格",
            "scenario": "AI 场景",
            "first_mes": "「你终于来了。」*顾尘按住剑柄。*",
            "mes_example": "<START>\n{{user}}: 你是谁？\n{{char}}: 一个路过的人。",
            "system_prompt": "你是《剑气长歌》中的顾尘。",
            "post_history_instructions": "不要 OOC。",
            "alternate_greetings": ["「又是你。」"],
            "tags": ["剑客", "主角"],
        }]),
    ))
    assert warnings == []
    card = cards[0]
    assert card["ai_filled"] is True
    assert card["description"] == "AI 生成的外貌与背景"
    assert "终于来了" in card["first_mes"]
    assert "{{char}}" in card["mes_example"]
    assert card["alternate_greetings"] == ["「又是你。」"]
    assert card["novel_id"] == "n1" and card["source"] == "novel"


def test_generate_cards_with_ai_falls_back_on_bad_output():
    cards, warnings = asyncio.run(novel_cards.generate_cards_with_ai(
        FakeLLMEngine(), PLAN, "n1", names=["顾尘", "苏晚"],
        resilient=FakeAIClient([]),
    ))
    assert len(cards) == 2
    assert warnings and "兜底" in warnings[0]
    assert all(not card.get("ai_filled") for card in cards)
    assert cards[0]["name"] == "顾尘"


def test_api_from_novel_ai_mode(api, tmp_path, monkeypatch):
    novels_dir = tmp_path / "novels"
    novel_dir = novels_dir / "novel-ai"
    novel_dir.mkdir(parents=True)
    (novel_dir / "plan.json").write_text(json.dumps(PLAN, ensure_ascii=False), encoding="utf-8")
    (novel_dir / "character_bible.json").write_text(json.dumps(BIBLE, ensure_ascii=False), encoding="utf-8")
    (novel_dir / "character_profiles.json").write_text(json.dumps(PROFILES, ensure_ascii=False), encoding="utf-8")
    (novel_dir / "character_voices.json").write_text(json.dumps(VOICES, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(api.rp, "NOVELS_DIR", str(novels_dir))

    async def fake_generate(llm_engine, plan, novel_id, **kwargs):
        return ([{
            "name": "顾尘", "description": "AI 补全描述", "personality": "AI 性格",
            "scenario": "AI 场景", "first_mes": "AI 开场", "mes_example": "",
            "system_prompt": "你是顾尘", "post_history_instructions": "",
            "alternate_greetings": [], "tags": ["AI"],
            "ai_filled": True, "source": "novel", "novel_id": novel_id,
        }], [])

    monkeypatch.setattr(api.rp.novel_cards, "generate_cards_with_ai", fake_generate)
    response = api.client.post(
        "/api/roleplay/characters/from-novel/novel-ai",
        json={"mode": "ai", "characters": ["顾尘"]},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["created"] == 1 and data["mode"] == "ai" and data["ai_filled"] == 1
    assert data["characters"][0]["description"] == "AI 补全描述"
    assert data["characters"][0]["ai_filled"] is True


# --------------------------------------------------- v2: commands / worldinfo

def test_command_parser_aliases_and_help():
    parsed = commands.parse_command("/s 你好")
    assert parsed and parsed.name == "say" and parsed.args == "你好"
    assert commands.parse_command("/do 推门").name == "act"
    assert commands.parse_command("/narrate 夜色更深").name == "nar"
    assert commands.parse_command("/dice 1d20").name == "roll"
    assert commands.parse_command("/unknown x").name == "unknown"
    assert commands.parse_command("普通消息") is None
    assert "/act" in commands.HELP_TEXT and "/swipe" in commands.HELP_TEXT


def test_action_detection():
    assert commands.looks_like_action("我推门进去，同时拔剑")
    assert commands.looks_like_action("（叹气）")
    assert commands.looks_like_action("上车")
    assert not commands.looks_like_action("你觉得这件事怎么样")
    assert not commands.looks_like_action("/act 推门")


def test_roll_dice(monkeypatch):
    monkeypatch.setattr(commands.random, "randint", lambda low, high: 4)
    result = commands.roll_dice("2d6+3")
    assert result["rolls"] == [4, 4] and result["total"] == 11
    assert "2d6+3" in result["text"]
    with pytest.raises(ValueError):
        commands.roll_dice("d")


def test_worldinfo_import_scan_and_format():
    payload = {
        "entries": {
            "0": {
                "uid": 0, "key": ["剑冢"], "keysecondary": ["顾尘"],
                "content": "剑冢是顾氏一族的禁地。", "comment": "剑冢",
                "selective": True, "selectiveLogic": 0, "order": 200,
            },
            "1": {
                "uid": 1, "key": ["苏晚"], "content": "苏晚是书斋主人。",
                "comment": "苏晚", "constant": True, "order": 100,
            },
            "2": {
                "uid": 2, "key": ["不可能出现的词"], "content": "隐藏设定。",
                "comment": "隐藏",
            },
        }
    }
    entries = worldinfo.parse_worldinfo_payload(payload)
    assert len(entries) == 3
    activated = worldinfo.scan_world_info(
        entries,
        [{"role": "user", "content": "我想去剑冢找顾尘"}],
        "顾尘拔剑",
        budget=5,
        scan_depth=6,
    )
    names = [entry["comment"] for entry in activated]
    assert "剑冢" in names and "苏晚" in names and "隐藏" not in names
    text = worldinfo.format_world_info(activated)
    assert "[世界书]" in text and "剑冢是顾氏一族的禁地" in text


def test_worldinfo_selective_logic_not_any():
    entry = worldinfo.normalize_entry({
        "uid": 9, "key": ["书斋"], "keysecondary": ["关门"],
        "content": "书斋已经关门。", "selective": True, "selectiveLogic": 2,
    })
    # NOT_ANY: secondary key present -> skip
    assert worldinfo.scan_world_info([entry], [], "书斋 关门") == []
    assert len(worldinfo.scan_world_info([entry], [], "书斋")) == 1


def test_worldinfo_selective_and_any_requires_secondary():
    entry = worldinfo.normalize_entry({
        "uid": 10, "key": ["书斋"], "keysecondary": ["苏晚"],
        "content": "苏晚在书斋。", "selective": True, "selectiveLogic": 0,
    })
    assert worldinfo.scan_world_info([entry], [], "书斋") == []
    assert len(worldinfo.scan_world_info([entry], [], "书斋 苏晚")) == 1


def test_prompt_renders_action_worldinfo_and_authors_note():
    card = card_codec.normalize_card(CARD_V2)
    world_entries = [worldinfo.normalize_entry({
        "uid": 1, "key": ["书斋"], "content": "书斋在旧城小巷。", "comment": "书斋",
    })]
    messages = prompt.build_messages(
        card,
        {
            "user_name": "阿明", "authors_note": "保持克制的文风",
            "authors_note_depth": 2, "context_messages": 20,
        },
        [
            {"role": "assistant", "content": "欢迎。"},
            {"role": "user", "content": "我推门进去。", "kind": "action"},
        ],
        world_entries,
        directive="DIRECTIVE",
    )
    assert any("[动作] 我推门进去" in item["content"] for item in messages)
    assert any("[世界书]" in item["content"] and "书斋在旧城小巷" in item["content"] for item in messages)
    assert any("[作者注释]" in item["content"] for item in messages)
    assert messages[-1] == {"role": "system", "content": "DIRECTIVE"}


def test_engine_command_roll_nar_help(tmp_path):
    store = RoleplayStore(str(tmp_path / "rp"))
    engine = RoleplayEngine(FakeLLMEngine(), store)
    engine.ensure_chat("builtin-suwan")

    events = _collect(engine, char_id="builtin-suwan", message="/roll 1d6")
    assert events[-1]["type"] == "done"
    assert any(event["type"] == "command_result" and event["command"] == "roll" for event in events)
    assert store.chat_messages("builtin-suwan")[-1]["kind"] == "roll"

    events = _collect(engine, char_id="builtin-suwan", message="/nar 夜色更深了")
    assert any(event["type"] == "message" and event["message"]["kind"] == "narration" for event in events)
    assert store.chat_messages("builtin-suwan")[-1]["content"] == "夜色更深了"

    events = _collect(engine, char_id="builtin-suwan", message="/help")
    assert any(event["type"] == "command_result" and "/swipe" in event["text"] for event in events)
    assert not any(event["type"] == "delta" for event in events)


def test_engine_action_message_and_swipe_variants(tmp_path):
    store = RoleplayStore(str(tmp_path / "rp"))
    engine = RoleplayEngine(FakeLLMEngine(), store)
    engine.ensure_chat("builtin-suwan")
    engine._resilient = FakeResilient(["第一版动作结果"])
    _collect(engine, char_id="builtin-suwan", message="/act 我推门进去，同时拔剑")

    messages = store.chat_messages("builtin-suwan")
    assert messages[-2]["kind"] == "action"
    assert messages[-1]["content"] == "第一版动作结果"

    engine._resilient = FakeResilient(["第二版动作结果"])
    events = _collect(engine, char_id="builtin-suwan", message="/swipe new")
    assert any(event["type"] == "swipe" and event["total"] == 2 for event in events)
    messages = store.chat_messages("builtin-suwan")
    assert messages[-1]["content"] == "第二版动作结果"
    assert len(messages[-1]["variants"]) == 2

    _collect(engine, char_id="builtin-suwan", message="/swipe prev")
    assert store.chat_messages("builtin-suwan")[-1]["content"] == "第一版动作结果"


def test_engine_injects_lorebook_and_authors_note(tmp_path):
    store = RoleplayStore(str(tmp_path / "rp"))
    store.save_worldinfo("builtin-suwan", [{
        "id": "wi-1", "keys": ["书斋"], "content": "书斋在旧城小巷深处。",
        "comment": "书斋", "enabled": True,
    }])
    store.update_settings({"authors_note": "保持克制的文风"})
    engine = RoleplayEngine(FakeLLMEngine(), store)
    capture = CapturingResilient()
    engine._resilient = capture
    engine.ensure_chat("builtin-suwan")
    _collect(engine, char_id="builtin-suwan", message="我走进书斋")
    system_text = "\n".join(m["content"] for m in capture.messages or [] if m["role"] == "system")
    assert "[世界书]" in system_text and "书斋在旧城小巷深处" in system_text
    assert "[作者注释]" in system_text and "保持克制" in system_text


def test_greeting_index_with_empty_first_mes(tmp_path):
    store = RoleplayStore(str(tmp_path / "rp"))
    card = store.get_character("builtin-suwan")
    card = dict(card)
    card["first_mes"] = ""
    card["alternate_greetings"] = ["备选一", "备选二"]
    store.upsert_character(card)
    engine = RoleplayEngine(FakeLLMEngine(), store)
    messages = engine.reset_chat("builtin-suwan", greeting_index=0)
    assert messages[0]["content"] == "备选一"


def test_regenerate_after_failed_send_appends_instead_of_overwriting_greeting(tmp_path):
    store = RoleplayStore(str(tmp_path / "rp"))
    engine = RoleplayEngine(FakeLLMEngine(), store)
    messages = engine.ensure_chat("builtin-suwan")
    greeting = messages[0]["content"]
    messages.append({"role": "user", "content": "你好", "kind": "dialogue"})
    store.save_chat("builtin-suwan", messages)

    engine._resilient = FakeResilient(["新回复"])
    _collect(engine, char_id="builtin-suwan", regenerate=True)
    messages = store.chat_messages("builtin-suwan")
    assert messages[0]["content"] == greeting
    assert messages[-1]["content"] == "新回复"
    assert sum(1 for m in messages if m["role"] == "assistant") == 2


def test_worldinfo_budget_respects_order(tmp_path):
    entries = [
        worldinfo.normalize_entry({
            "uid": 1, "key": ["书斋"], "content": "低优先级", "comment": "低", "order": 1,
        }),
        worldinfo.normalize_entry({
            "uid": 2, "key": ["书斋"], "content": "高优先级", "comment": "高", "order": 999,
        }),
    ]
    activated = worldinfo.scan_world_info(entries, [], "书斋", budget=1)
    assert [entry["comment"] for entry in activated] == ["高"]


def test_engine_rejects_overlapping_generation(tmp_path):
    store = RoleplayStore(str(tmp_path / "rp"))
    engine = RoleplayEngine(FakeLLMEngine(), store)
    engine.ensure_chat("builtin-suwan")
    assert store.begin_generation("builtin-suwan")
    try:
        events = _collect(engine, char_id="builtin-suwan", message="你好")
    finally:
        store.end_generation("builtin-suwan")
    assert events[0] == {
        "type": "error",
        "code": "busy",
        "message": "这个角色正在回复，请等待或停止当前生成",
    }


def test_api_import_card_with_character_book(api):
    card = json.loads(json.dumps(CARD_V2))
    card["data"]["character_book"] = {
        "entries": [{
            "uid": 1, "key": ["书斋"], "content": "书斋在旧城小巷。",
            "comment": "书斋", "selective": False,
        }]
    }
    payload = base64.b64encode(json.dumps(card, ensure_ascii=False).encode()).decode()
    response = api.client.post(
        "/api/roleplay/characters/import",
        json={"filename": "card.json", "data_base64": payload},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["worldinfo_imported"] == 1
    char_id = data["character"]["id"]
    entries = api.client.get(f"/api/roleplay/characters/{char_id}/worldinfo").json()["entries"]
    assert entries[0]["comment"] == "书斋"


def test_api_worldinfo_import_patch_delete(api):
    client = api.client
    payload = json.dumps({"entries": [
        {"uid": 1, "key": ["书斋"], "content": "书斋在旧城小巷。", "comment": "书斋"},
        {"uid": 2, "key": ["铜铃"], "content": "铜铃会响。", "comment": "铜铃"},
    ]}, ensure_ascii=False).encode("utf-8")
    response = client.post(
        "/api/roleplay/characters/builtin-suwan/worldinfo/import",
        json={"filename": "world.json", "data_base64": base64.b64encode(payload).decode()},
    )
    assert response.status_code == 200
    assert response.json()["imported"] == 2

    entries = client.get("/api/roleplay/characters/builtin-suwan/worldinfo").json()["entries"]
    target = next(entry for entry in entries if entry["comment"] == "书斋")
    patched = client.patch(
        f"/api/roleplay/characters/builtin-suwan/worldinfo/{target['id']}",
        json={"enabled": False},
    )
    assert patched.status_code == 200
    assert patched.json()["entry"]["enabled"] is False

    deleted = client.delete(f"/api/roleplay/characters/builtin-suwan/worldinfo/{target['id']}")
    assert deleted.status_code == 200
    remaining = client.get("/api/roleplay/characters/builtin-suwan/worldinfo").json()["entries"]
    assert all(entry["id"] != target["id"] for entry in remaining)


def test_api_chat_swipe_next(api):
    client = api.client
    api.engine._resilient = FakeResilient(["第一版"])
    client.post("/api/roleplay/characters/builtin-suwan/chat/start")
    client.post(
        "/api/roleplay/characters/builtin-suwan/chat/send",
        json={"message": "你好"},
    )
    api.engine._resilient = FakeResilient(["第二版"])
    with client.stream("POST", "/api/roleplay/characters/builtin-suwan/chat/regenerate") as response:
        assert response.status_code == 200
        "".join(response.iter_text())

    response = client.post(
        "/api/roleplay/characters/builtin-suwan/chat/swipe",
        json={"direction": "next"},
    )
    assert response.status_code == 200
    messages = response.json()["messages"]
    assert messages[-1]["role"] == "assistant"
    assert messages[-1]["content"] == "第一版"
    assert len(messages[-1]["variants"]) == 2
