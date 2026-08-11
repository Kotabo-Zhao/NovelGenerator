"""Tests for the persistent generation-job state contract."""

import tempfile
import unittest
from pathlib import Path
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from core.job_store import GenerationJobStore, JobStatus


class GenerationJobStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = GenerationJobStore(Path(self.temp_dir.name))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_create_or_get_is_idempotent(self):
        first = self.store.create_or_get(
            job_type="chapter",
            novel_id="novel-1",
            request={"chapter_number": 1, "target_words": 1200},
            idempotency_key="retry-001",
        )
        second = self.store.create_or_get(
            job_type="chapter",
            novel_id="novel-1",
            request={"chapter_number": 1, "target_words": 1200},
            idempotency_key="retry-001",
        )

        self.assertEqual(first["job_id"], second["job_id"])
        self.assertEqual(first["status"], JobStatus.QUEUED)
        self.assertEqual(first["request"], {"chapter_number": 1, "target_words": 1200})

    def test_idempotency_key_cannot_change_job_identity(self):
        self.store.create_or_get(
            job_type="chapter",
            novel_id="novel-1",
            request={"chapter_number": 1},
            idempotency_key="retry-002",
        )

        with self.assertRaises(ValueError):
            self.store.create_or_get(
                job_type="chapter",
                novel_id="novel-2",
                request={"chapter_number": 1},
                idempotency_key="retry-002",
            )

    def test_idempotency_scope_isolated_by_owner(self):
        first = self.store.create_or_get(
            job_type="chapter",
            novel_id="novel-1",
            request={"chapter_number": 1},
            owner_id="wx_owner_a",
            idempotency_key="retry-003",
        )
        second = self.store.create_or_get(
            job_type="chapter",
            novel_id="novel-1",
            request={"chapter_number": 1},
            owner_id="wx_owner_b",
            idempotency_key="retry-003",
        )

        self.assertNotEqual(first["job_id"], second["job_id"])

    def test_lifecycle_and_progress_are_persisted(self):
        job = self.store.create_or_get(
            job_type="chapter",
            novel_id="novel-1",
            request={"chapter_number": 2},
        )
        job_id = job["job_id"]

        self.store.mark_running(job_id, phase="drafting")
        self.store.update_progress(job_id, progress=42, phase="polishing")
        completed = self.store.complete(job_id, result={"chapter_id": "chapter-2"})

        self.assertEqual(completed["status"], JobStatus.SUCCEEDED)
        self.assertEqual(completed["progress"], 100)
        self.assertEqual(completed["result"], {"chapter_id": "chapter-2"})
        self.assertIsNotNone(completed["started_at"])
        self.assertIsNotNone(completed["finished_at"])
        self.assertEqual(self.store.get(job_id), completed)

    def test_cancel_request_is_visible_to_worker(self):
        job = self.store.create_or_get(
            job_type="chapter",
            novel_id="novel-1",
            request={"chapter_number": 3},
        )
        job_id = job["job_id"]
        self.store.mark_running(job_id, phase="drafting")

        requested = self.store.request_cancel(job_id)
        self.assertEqual(requested["status"], JobStatus.CANCEL_REQUESTED)
        self.assertTrue(self.store.is_cancel_requested(job_id))

        cancelled = self.store.mark_cancelled(job_id)
        self.assertEqual(cancelled["status"], JobStatus.CANCELLED)
        self.assertFalse(self.store.is_cancel_requested(job_id))

    def test_invalid_transition_is_rejected(self):
        job = self.store.create_or_get(
            job_type="chapter",
            novel_id="novel-1",
            request={"chapter_number": 4},
        )

        with self.assertRaises(ValueError):
            self.store.complete(job["job_id"], result={"text": "not ready"})

    def test_a_second_store_instance_reads_the_same_job(self):
        job = self.store.create_or_get(
            job_type="outline",
            novel_id="novel-1",
            request={"title": "测试"},
        )
        second_store = GenerationJobStore(Path(self.temp_dir.name))

        self.assertEqual(second_store.get(job["job_id"]), job)


if __name__ == "__main__":
    unittest.main()
