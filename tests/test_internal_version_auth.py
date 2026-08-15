"""Regression tests for the explicitly enabled internal build."""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from fastapi.testclient import TestClient

from api.server import app


class InternalVersionAuthTests(unittest.TestCase):
    def test_internal_build_does_not_require_wechat_login(self):
        with patch.dict(os.environ, {"NOVEL_GENERATOR_INTERNAL": "true"}):
            client = TestClient(app)
            response = client.get("/api/novels")
        self.assertNotEqual(response.status_code, 401)

    def test_internal_build_does_not_require_privacy_consent(self):
        with patch.dict(os.environ, {"NOVEL_GENERATOR_INTERNAL": "true"}):
            client = TestClient(app)
            response = client.get("/api/auth/me")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["consent_required"])

    def test_internal_build_lists_all_local_novels(self):
        local_novels = [
            {"id": "book-a", "title": "本地作品 A"},
            {"id": "book-b", "title": "本地作品 B"},
        ]
        with (
            patch.dict(os.environ, {"NOVEL_GENERATOR_INTERNAL": "true"}),
            patch("api.routers.novels.engine.list_novels", return_value=local_novels),
        ):
            client = TestClient(app)
            response = client.get("/api/novels")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["novels"], local_novels)

    def test_default_build_still_requires_login(self):
        with patch.dict(os.environ, {"NOVEL_GENERATOR_INTERNAL": "false"}):
            client = TestClient(app)
            response = client.get("/api/novels")
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
