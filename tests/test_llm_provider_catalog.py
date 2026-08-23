"""统一 LLM 供应商目录与运行时切换回归测试。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from fastapi.testclient import TestClient

from api.server import app, _LLM_CONFIG_FILE
from api.deps import engine
from core.llm_providers import is_configured_key


class LLMProviderCatalogTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.original = {
            "provider": engine.provider,
            "api_key": engine.api_key,
            "base_url": engine.base_url,
            "model": engine.model,
        }

    def tearDown(self):
        key = self.original["api_key"] if is_configured_key(self.original["api_key"]) else None
        engine.set_provider_config(
            self.original["provider"],
            api_key=key,
            base_url=self.original["base_url"],
            model=self.original["model"],
        )
        if not is_configured_key(self.original["api_key"]):
            # 仅恢复测试占位状态，不把占位 Key 写入持久化文件。
            engine.api_key = self.original["api_key"]
            engine.client.api_key = self.original["api_key"] or "sk-placeholder-for-tests"
        try:
            if os.path.exists(_LLM_CONFIG_FILE):
                os.remove(_LLM_CONFIG_FILE)
        except OSError:
            pass

    def test_catalog_contains_dots_and_mainstream_presets(self):
        response = self.client.get("/api/config/llm/providers")
        self.assertEqual(response.status_code, 200)
        providers = {item["id"]: item for item in response.json()["providers"]}
        for provider_id in ("deepseek", "dots", "openai", "qwen", "moonshot", "zhipu", "doubao", "custom"):
            self.assertIn(provider_id, providers)
        self.assertEqual(providers["dots"]["base_url"], "https://note3-prev-api.askdiandian.com/v1")
        self.assertEqual(providers["dots"]["models"], ["dots3-note-prev"])
        self.assertEqual(providers["dots"]["auth_header"], "api-key")

    def test_dots_config_accepts_non_sk_key_and_syncs_pipeline(self):
        response = self.client.post(
            "/api/config/llm",
            json={
                "provider": "dots",
                "api_key": "dots-test-key-123",
                "model": "dots3-note-prev",
            },
        )
        self.assertEqual(response.status_code, 200)
        config = response.json()["config"]
        self.assertEqual(config["provider"], "dots")
        self.assertEqual(config["model"], "dots3-note-prev")
        self.assertTrue(config["api_key_configured"])
        self.assertNotIn("api_key", config)
        self.assertEqual(engine.base_url, "https://note3-prev-api.askdiandian.com/v1")
        self.assertEqual(engine.model, "dots3-note-prev")
        self.assertEqual(engine.provider, "dots")
        for component in engine._sub_components():
            if component is not None and hasattr(component, "client"):
                self.assertIs(component.client, engine.client)

    def test_custom_http_endpoint_only_allows_loopback(self):
        response = self.client.post(
            "/api/config/llm",
            json={
                "provider": "custom",
                "api_key": "custom-test-key-123",
                "base_url": "http://10.0.0.8/v1",
                "model": "local-model",
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_switching_provider_does_not_reuse_another_provider_key(self):
        first = self.client.post(
            "/api/config/llm",
            json={"provider": "dots", "api_key": "dots-test-key-123", "model": "dots3-note-prev"},
        )
        self.assertEqual(first.status_code, 200)
        second = self.client.post(
            "/api/config/llm",
            json={"provider": "openai", "model": "gpt-5.2"},
        )
        self.assertEqual(second.status_code, 200)
        self.assertFalse(second.json()["config"]["api_key_configured"])

    def test_connection_test_never_returns_key(self):
        # 不调用外部服务：未配置凭据时应在本地快速失败，响应不包含任何敏感字段。
        original_key = engine.api_key
        engine.api_key = ""
        try:
            response = self.client.post("/api/config/llm/test")
        finally:
            engine.api_key = original_key
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("api_key", response.text.lower())


if __name__ == "__main__":
    unittest.main()
