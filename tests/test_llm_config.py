"""LLM 运行时配置（设置页 API Key + 模型切换）回归测试。"""

import importlib.util
import importlib.machinery
import os
import sys
import tempfile
import types
import unittest

if importlib.util.find_spec("openai") is None:
    fake_openai = types.ModuleType("openai")
    fake_openai.__spec__ = importlib.machinery.ModuleSpec("openai", loader=None)

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

    fake_openai.OpenAI = FakeClient
    for _name in ("APIError", "RateLimitError", "APITimeoutError", "APIConnectionError"):
        setattr(fake_openai, _name, type(_name, (Exception,), {}))
    sys.modules["openai"] = fake_openai
if importlib.util.find_spec("dotenv") is None:
    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.__spec__ = importlib.machinery.ModuleSpec("dotenv", loader=None)
    fake_dotenv.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = fake_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from fastapi.testclient import TestClient
from api.server import app
from api.deps import engine

_LLM_CONFIG_FILE = os.path.join(
    os.path.dirname(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))),
    "llm_config.json",
)


class LLMConfigTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self._orig_key = engine.client.api_key
        self._orig_model = engine.model

    def tearDown(self):
        engine.client.api_key = self._orig_key
        engine.model = self._orig_model
        engine.set_model(self._orig_model)
        # 清理测试写入的持久化文件，避免污染本地/Android 启动配置
        try:
            if os.path.exists(_LLM_CONFIG_FILE):
                os.remove(_LLM_CONFIG_FILE)
        except Exception:
            pass

    def test_get_config_returns_status_without_leaking_key(self):
        r = self.client.get("/api/config/llm")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertIn("model", d)
        self.assertIn("allowed_models", d)
        self.assertIn("api_key_configured", d)
        self.assertNotIn("api_key", d)
        self.assertEqual(d["allowed_models"], ["deepseek-chat", "deepseek-v4-flash"])

    def test_set_model_switches_runtime_and_syncs_subcomponents(self):
        r = self.client.post("/api/config/llm", json={"model": "deepseek-chat"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["config"]["model"], "deepseek-chat")
        # 子组件 model 同步
        for comp in engine._sub_components():
            if comp is not None:
                self.assertEqual(comp.model, "deepseek-chat")
        # engine 状态
        self.assertEqual(engine.model, "deepseek-chat")

    def test_invalid_model_rejected(self):
        r = self.client.post("/api/config/llm", json={"model": "gpt-4"})
        self.assertEqual(r.status_code, 400)

    def test_set_api_key_updates_client(self):
        r = self.client.post("/api/config/llm", json={"api_key": "sk-test-abc123"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(engine.client.api_key, "sk-test-abc123")
        self.assertTrue(r.json()["config"]["api_key_configured"])

    def test_invalid_api_key_rejected(self):
        r = self.client.post("/api/config/llm", json={"api_key": "not-a-key"})
        self.assertEqual(r.status_code, 400)

    def test_empty_body_rejected(self):
        r = self.client.post("/api/config/llm", json={})
        self.assertEqual(r.status_code, 400)

    def test_health_reports_allowed_models(self):
        r = self.client.get("/api/health")
        d = r.json()
        self.assertIn("allowed_models", d)
        self.assertEqual(d["allowed_models"], ["deepseek-chat", "deepseek-v4-flash"])


if __name__ == "__main__":
    unittest.main()
