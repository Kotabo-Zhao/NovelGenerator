"""Tests for durable, non-inline export jobs."""

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from core.export_job_runner import ExportJobRunner
from core.job_store import GenerationJobStore, JobStatus


class FakeEngine:
    def export_novel(self, novel_id, fmt):
        return f"{novel_id}:{fmt}", None


class ExportJobRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.store = GenerationJobStore(root, "jobs.db")
        self.output_root = root / "exports"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_export_writes_file_and_only_stores_metadata(self):
        job = self.store.create_or_get(
            job_type="export",
            novel_id="novel-1",
            request={"fmt": "txt"},
            owner_id="wx_owner_a",
        )

        asyncio.run(
            ExportJobRunner(FakeEngine(), self.store, self.output_root).run_export(
                job["job_id"], "wx_owner_a", "novel-1", "txt"
            )
        )
        result = self.store.get(job["job_id"])

        self.assertEqual(result["status"], JobStatus.SUCCEEDED)
        self.assertEqual(result["result"]["size_bytes"], len("novel-1:txt".encode("utf-8")))
        self.assertTrue(Path(result["result"]["path"]).is_file())
        self.assertNotIn("content", result["result"])

    def test_second_worker_cannot_duplicate_an_export(self):
        job = self.store.create_or_get(
            job_type="export",
            novel_id="novel-1",
            request={"fmt": "txt"},
            owner_id="wx_owner_a",
        )
        self.store.try_claim_running(job["job_id"])

        asyncio.run(
            ExportJobRunner(FakeEngine(), self.store, self.output_root).run_export(
                job["job_id"], "wx_owner_a", "novel-1", "txt"
            )
        )

        self.assertEqual(self.store.get(job["job_id"])["status"], JobStatus.RUNNING)


if __name__ == "__main__":
    unittest.main()
