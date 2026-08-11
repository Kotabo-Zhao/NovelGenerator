"""HTTP regression tests for the legacy novel compatibility surface."""

import importlib.util
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

if importlib.util.find_spec("openai") is None:
    fake_openai = types.ModuleType("openai")

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

    fake_openai.OpenAI = FakeClient
    for _name in ("APIError", "RateLimitError", "APITimeoutError", "APIConnectionError"):
        setattr(fake_openai, _name, type(_name, (Exception,), {}))
    sys.modules["openai"] = fake_openai
if importlib.util.find_spec("dotenv") is None:
    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = fake_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from fastapi.testclient import TestClient

from api.server import app
from api.routers import novels
from core.ownership_store import OwnershipStore


class FakeEngine:
    def list_novels(self):
        return [
            {"id": "novel-a", "title": "A"},
            {"id": "novel-b", "title": "B"},
        ]

    def get_novel(self, novel_id):
        return {"title": novel_id, "outline": {}}


class LegacyOwnerApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = OwnershipStore(self.temp_dir.name, "ownership.db")
        self.store.claim("novel-a", "owner-a")
        self.store.claim("novel-b", "owner-b")
        self.old_server_store = __import__("api.server", fromlist=["ownership_store"]).ownership_store
        self.old_router_store = novels.ownership_store
        self.old_engine = novels.engine
        self.server_store_module = __import__("api.server", fromlist=["ownership_store"])
        self.server_store_module.ownership_store = self.store
        novels.ownership_store = self.store
        novels.engine = FakeEngine()
        self.client = TestClient(app)

    def tearDown(self):
        self.server_store_module.ownership_store = self.old_server_store
        novels.ownership_store = self.old_router_store
        novels.engine = self.old_engine
        self.temp_dir.cleanup()

    def test_legacy_novel_routes_require_login(self):
        response = self.client.get("/api/novels")
        self.assertEqual(response.status_code, 401)

    def test_legacy_list_and_detail_are_owner_scoped(self):
        with patch("api.server.owner_id_from_authorization", return_value="owner-a"):
            listing = self.client.get("/api/novels", headers={"Authorization": "Bearer test"})
            detail = self.client.get("/api/novels/novel-a", headers={"Authorization": "Bearer test"})
            foreign = self.client.get("/api/novels/novel-b", headers={"Authorization": "Bearer test"})

        self.assertEqual(listing.status_code, 200)
        self.assertEqual([item["id"] for item in listing.json()["novels"]], ["novel-a"])
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(foreign.status_code, 404)
        self.assertEqual(foreign.json()["detail"], "小说不存在")


if __name__ == "__main__":
    unittest.main()
