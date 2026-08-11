"""Focused contract tests for the authenticated v1 migration API."""

import asyncio
import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

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

from fastapi import HTTPException
from pydantic import ValidationError

from core.job_store import GenerationJobStore, JobStatus
from core.ownership_store import OwnershipStore
from api.routers import v1


class FakeMemory:
    def get_novel_state(self, novel_id):
        return {"status": "planning_done", "completed_chapters": []}


class FakeEngine:
    def __init__(self):
        self.memory = FakeMemory()
        self.calls = 0
        self.create_calls = 0

    def get_novel(self, novel_id):
        return {"title": novel_id, "genre": "测试", "style": "测试风格"}

    def get_chapter(self, novel_id, chapter_num):
        return "正文"

    def create_novel(self, creative_input):
        self.create_calls += 1
        return {"title": "novel-created", "genre": creative_input["genre"], "style": creative_input["style"]}

    async def generate_chapter_stream(self, novel_id, chapter_num, writing_mode, feedback=None):
        self.calls += 1
        yield {"type": "progress", "phase": "writing", "pct": 50}
        yield {"type": "done", "chapter_num": chapter_num, "content": "正文"}


class V1ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_engine = v1.engine
        self.old_job_store = v1.job_store
        self.old_ownership_store = v1.ownership_store
        self.engine = FakeEngine()
        self.job_store = GenerationJobStore(Path(self.temp_dir.name), "jobs.db")
        self.ownership_store = OwnershipStore(Path(self.temp_dir.name), "ownership.db")
        v1.engine = self.engine
        v1.job_store = self.job_store
        v1.ownership_store = self.ownership_store

    def tearDown(self):
        v1.engine = self.old_engine
        v1.job_store = self.old_job_store
        v1.ownership_store = self.old_ownership_store
        v1._active_tasks.clear()
        self.temp_dir.cleanup()

    def test_public_job_response_does_not_leak_owner(self):
        job = self.job_store.create_or_get(
            job_type="chapter",
            novel_id="novel-1",
            request={"chapter_num": 1},
            owner_id="wx_owner_a",
        )

        public = v1._public_job(job)

        self.assertNotIn("owner_id", public)
        self.assertEqual(public["job_id"], job["job_id"])

    def test_cross_owner_job_query_is_hidden_as_not_found(self):
        job = self.job_store.create_or_get(
            job_type="chapter",
            novel_id="novel-1",
            request={"chapter_num": 1},
            owner_id="wx_owner_a",
        )

        with self.assertRaises(HTTPException) as context:
            v1._owned_job(job["job_id"], "wx_owner_b")

        self.assertEqual(context.exception.status_code, 404)
        self.assertEqual(context.exception.detail, "任务不存在")

    def test_invalid_chapter_job_request_is_rejected_at_schema_boundary(self):
        with self.assertRaises(ValidationError):
            v1.ChapterJobRequest(chapter_num=0)
        with self.assertRaises(ValidationError):
            v1.ChapterJobRequest(chapter_num=1, writing_mode="unknown")

    def test_chapter_job_route_starts_runner_and_is_idempotent(self):
        self.ownership_store.claim("novel-1", "wx_owner_a")
        request = v1.ChapterJobRequest(chapter_num=1)

        async def run_flow():
            first_job = await v1.create_chapter_job(
                "novel-1", request, "retry-001", "wx_owner_a"
            )
            await asyncio.sleep(0.02)
            second_job = await v1.create_chapter_job(
                "novel-1", request, "retry-001", "wx_owner_a"
            )
            return first_job, second_job

        first, second = asyncio.run(run_flow())
        stored = self.job_store.get(first["job_id"])

        self.assertEqual(first["job_id"], second["job_id"])
        self.assertEqual(stored["status"], JobStatus.SUCCEEDED)
        self.assertEqual(self.engine.calls, 1)

    def test_novel_creation_requires_idempotency_and_replays_success(self):
        from api.routers.novels import CreateNovelRequest

        request = CreateNovelRequest(inspiration="一个测试灵感")

        async def run_flow():
            first_result = await v1.create_owned_novel(request, "create-001", "wx_owner_a")
            second_result = await v1.create_owned_novel(request, "create-001", "wx_owner_a")
            return first_result, second_result

        first, second = asyncio.run(run_flow())

        self.assertEqual(first["novel_id"], "novel-created")
        self.assertFalse(first["replayed"])
        self.assertTrue(second["replayed"])
        self.assertEqual(self.engine.create_calls, 1)


if __name__ == "__main__":
    unittest.main()
