"""Behavior tests for the persistent chapter-job worker adapter."""

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from core.job_store import GenerationJobStore, JobStatus
from core.job_runner import ChapterJobRunner


class FakeEngine:
    def __init__(self, events):
        self.events = events
        self.calls = []

    async def generate_chapter_stream(self, novel_id, chapter_num, writing_mode, feedback=None):
        self.calls.append((novel_id, chapter_num, writing_mode, feedback))
        for event in self.events:
            yield event


class ChapterJobRunnerTests(unittest.TestCase):
    def run_async(self, coroutine):
        return asyncio.run(coroutine)

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = GenerationJobStore(Path(self.temp_dir.name))

    def tearDown(self):
        self.temp_dir.cleanup()

    def create_job(self):
        return self.store.create_or_get(
            job_type="chapter",
            novel_id="novel-1",
            request={"chapter_num": 1, "writing_mode": "webnovel"},
            owner_id="wx_owner_a",
        )

    def test_successful_stream_completes_the_job_without_storing_full_text(self):
        job = self.create_job()
        engine = FakeEngine(
            [
                {"type": "status", "message": "开始"},
                {"type": "progress", "phase": "writing", "pct": 45},
                {"type": "done", "chapter_num": 1, "content": "很长的正文"},
            ]
        )

        self.run_async(
            ChapterJobRunner(engine, self.store).run_chapter(
                job["job_id"], "novel-1", 1, feedback="节奏快一点"
            )
        )
        result = self.store.get(job["job_id"])

        self.assertEqual(result["status"], JobStatus.SUCCEEDED)
        self.assertEqual(result["progress"], 100)
        self.assertEqual(result["result"], {"novel_id": "novel-1", "chapter_num": 1})
        self.assertEqual(engine.calls[0][3], "节奏快一点")

    def test_engine_error_fails_the_job(self):
        job = self.create_job()
        engine = FakeEngine([{"type": "error", "message": "上游暂时不可用"}])

        self.run_async(ChapterJobRunner(engine, self.store).run_chapter(job["job_id"], "novel-1", 1))
        result = self.store.get(job["job_id"])

        self.assertEqual(result["status"], JobStatus.FAILED)
        self.assertEqual(result["error_code"], "GENERATION_ERROR")
        self.assertEqual(result["error_message"], "生成章节失败，请稍后重试")

    def test_cancel_request_stops_before_consuming_the_next_event(self):
        job = self.create_job()
        self.store.request_cancel(job["job_id"])
        engine = FakeEngine([{"type": "done", "chapter_num": 1, "content": "不应执行"}])

        self.run_async(ChapterJobRunner(engine, self.store).run_chapter(job["job_id"], "novel-1", 1))
        result = self.store.get(job["job_id"])

        self.assertEqual(result["status"], JobStatus.CANCELLED)
        self.assertEqual(engine.calls, [])

    def test_stream_without_done_event_is_failed_closed(self):
        job = self.create_job()
        engine = FakeEngine([{"type": "status", "message": "只生成了一半"}])

        self.run_async(ChapterJobRunner(engine, self.store).run_chapter(job["job_id"], "novel-1", 1))
        result = self.store.get(job["job_id"])

        self.assertEqual(result["status"], JobStatus.FAILED)
        self.assertEqual(result["error_code"], "INCOMPLETE_RESULT")


if __name__ == "__main__":
    unittest.main()
