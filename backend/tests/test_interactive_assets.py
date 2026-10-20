"""Tests for interactive asset export and live LLM client refresh."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("ROLEPLAY_DIR", os.path.join(os.path.dirname(__file__), "_rp_assets_tmp"))

from core.interactive import novel_assets  # noqa: E402


def _write(path: str, payload: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


def test_load_assets_and_sync(tmp_path, monkeypatch):
    novels = tmp_path / "novels"
    novel_dir = novels / "book-1"
    novel_dir.mkdir(parents=True)
    _write(str(novel_dir / "plan.json"), {
        "title": "测试书", "genre": "武侠",
        "worldbuilding": {"era": "古代", "factions": ["听雨楼"]},
        "characters": {
            "protagonist": {"name": "顾尘", "identity": "剑客"},
            "supporting": [{"name": "苏晚", "identity": "医女"}],
        },
    })
    _write(str(novel_dir / "bible.json"), {
        "characters": {"顾尘": {"name": "顾尘", "identity": "剑客"}},
        "locations": {"听雨楼": {"description": "边城酒楼"}},
        "terms": {"剑冢": {"category": "地点", "description": "禁地"}},
        "relations": [{"from": "顾尘", "to": "苏晚", "relation": "旧识"}],
    })
    _write(str(novel_dir / "character_bible.json"), {
        "protagonist": {"name": "顾尘", "identity": "剑客"},
    })
    _write(str(novel_dir / "character_profiles.json"), {
        "顾尘": {"mental_models": ["世界是棋局"]},
    })
    _write(str(novel_dir / "character_voices.json"), {
        "顾尘": {"catchphrases": ["剑在人在"]},
    })
    monkeypatch.setattr(novel_assets, "NOVELS_DIR", str(novels))
    monkeypatch.setattr(novel_assets, "_roleplay_cards", lambda *_args: {
        "顾尘": {"description": "卡片描述", "personality": "冷淡", "avatar_url": ""},
    })

    assets = novel_assets.load_novel_assets("book-1")
    assert assets["plan"]["title"] == "测试书"
    assert "听雨楼" in assets["worldbook_brief"]
    assert "剑冢" in assets["worldbook_brief"]
    assert assets["roleplay_cards"]["顾尘"]["description"] == "卡片描述"

    state = {"player_char": {"name": "苏晚"}, "casts": {}}
    result = novel_assets.sync_assets_to_state(state, assets)
    assert result["cast_added"] >= 1
    assert "顾尘" in state["casts"]
    assert state["casts"]["顾尘"]["profile"]["mental_models"] == ["世界是棋局"]
    assert state["casts"]["顾尘"]["voice"]["catchphrases"] == ["剑在人在"]
    assert "世界书" in state["worldbuilding_brief"]


def test_interactive_clients_follow_live_engine(monkeypatch):
    from api.routers import interactive

    class FakeEngine:
        client = object()
        model = "live-model"

    for component in (interactive._story, interactive._chat, interactive._chat.action):
        component.client = object()
        component.model = "old-model"
        if getattr(component, "_resilient", None) is not None:
            component._resilient.client = object()
            component._resilient.model = "old-model"

    monkeypatch.setattr(interactive, "engine", FakeEngine())
    interactive._sync_interactive_clients()
    for component in (interactive._story, interactive._chat, interactive._chat.action):
        assert component.client is FakeEngine.client
        assert component.model == "live-model"
        if getattr(component, "_resilient", None) is not None:
            assert component._resilient.client is FakeEngine.client
            assert component._resilient.model == "live-model"
