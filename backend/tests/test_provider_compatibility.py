"""Settings and the legacy connection dialog must update the same live provider."""
import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def provider_client(monkeypatch, tmp_path):
    from api import server
    from api.deps import engine

    original = {"provider": engine.provider, "api_key": engine.api_key,
                "base_url": engine.base_url, "model": engine.model}
    monkeypatch.setenv("NOVEL_GENERATOR_INTERNAL", "true")
    config_file = tmp_path / "llm_config.json"
    monkeypatch.setattr(server, "_LLM_CONFIG_FILE", str(config_file))
    try:
        yield TestClient(server.app), config_file
    finally:
        engine.set_provider_config(**original)


def test_both_settings_interfaces_persist_and_switch_roleplay(provider_client):
    from api.deps import engine
    from api.routers.roleplay import _engine as roleplay

    client, path = provider_client
    response = client.put("/api/system/connection", json={
        "provider": "custom", "base_url": "http://127.0.0.1:8792/v1",
        "api_key": "local-regression-key", "model": "first-model",
    })
    assert response.status_code == 200
    assert response.json()["model"] == "first-model"
    assert "api_key" not in response.json()
    assert client.get("/api/config/llm").json()["model"] == "first-model"
    assert json.loads(path.read_text(encoding="utf-8"))["model"] == "first-model"

    response = client.post("/api/config/llm", json={
        "provider": "custom", "base_url": "http://127.0.0.1:8792/v1",
        "model": "second-model",
    })
    assert response.status_code == 200
    assert client.get("/api/system/connection").json()["model"] == "second-model"
    assert engine.writer.model == "second-model"
    assert roleplay.active_model() == "second-model"
    assert roleplay._resilient_client().client is engine.client
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["model"] == "second-model"
    assert saved["api_key"] == "local-regression-key"


def test_settings_do_not_switch_provider_during_roleplay_generation(provider_client, monkeypatch):
    from api.deps import engine
    from api.routers import roleplay

    client, path = provider_client
    before = engine.model
    monkeypatch.setattr(roleplay._store, "_generating", {"busy-character"})
    response = client.post("/api/config/llm", json={"provider": "custom", "model": "blocked-model"})
    assert response.status_code == 409
    assert engine.model == before
    assert not path.exists()
