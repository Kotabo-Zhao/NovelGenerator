"""HTTP-level checks for the public API security baseline."""

import importlib.util
import importlib.machinery
import os
import sys
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


class ApiSecurityBaselineTests(unittest.TestCase):
    def test_health_response_has_security_headers_and_no_storage_details(self):
        response = TestClient(app).get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])
        self.assertNotIn("storage", response.json())
        self.assertNotIn("novels", response.json())

    def test_legacy_tool_mutations_require_a_session(self):
        client = TestClient(app)
        protected = [
            ("post", "/api/styles/build-custom"),
            ("post", "/api/styles/seeds"),
            ("delete", "/api/styles/seeds/example"),
            ("post", "/api/styles/fingerprint"),
            ("post", "/api/styles/compare"),
            ("post", "/api/trends/analyze"),
            ("post", "/api/trends/bizarre"),
            ("post", "/api/trends/bizarre/quick"),
            ("post", "/api/trends/bizarre/create"),
            ("post", "/api/xiaohongshu/create"),
            ("post", "/api/xiaohongshu/titles"),
            ("post", "/api/tts"),
            ("post", "/api/tts/emotion"),
        ]
        for method, path in protected:
            with self.subTest(path=path):
                response = client.request(method.upper(), path, json={})
                self.assertEqual(response.status_code, 401)

    def test_oversized_request_is_rejected_before_route_processing(self):
        response = TestClient(app).post(
            "/api/auth/wechat-login",
            content=b"x" * 1_000_001,
            headers={"content-type": "application/json"},
        )
        self.assertEqual(response.status_code, 413)


if __name__ == "__main__":
    unittest.main()
