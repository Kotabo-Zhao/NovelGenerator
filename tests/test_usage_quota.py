"""Regression tests for durable daily generation reservations."""

import hashlib
import tempfile
import unittest

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from core.usage_quota import QuotaExceeded, UsageQuotaStore


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class UsageQuotaTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = UsageQuotaStore(self.temp_dir.name, "quota.db")

    def tearDown(self):
        self.temp_dir.cleanup()

    def reserve(self, key: str, value: str = "request"):
        return self.store.reserve(
            owner_id="owner-a",
            reservation_key=key,
            request_hash=digest(value),
            token_budget=4_000,
            daily_job_limit=2,
            daily_token_limit=8_000,
        )

    def test_same_idempotency_key_does_not_consume_twice(self):
        first = self.reserve("job-1")
        replay = self.reserve("job-1")

        self.assertFalse(first["replayed"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(self.store.get_usage("owner-a")["jobs_reserved"], 1)

    def test_different_request_under_same_key_is_rejected(self):
        self.reserve("job-1", "first")
        with self.assertRaises(ValueError):
            self.reserve("job-1", "second")

    def test_daily_job_and_token_limits_are_enforced(self):
        self.reserve("job-1")
        self.reserve("job-2", "request-2")
        with self.assertRaises(QuotaExceeded):
            self.reserve("job-3", "request-3")

    def test_daily_token_limit_is_enforced_independently(self):
        self.store.reserve(
            owner_id="owner-a",
            reservation_key="job-1",
            request_hash=digest("request"),
            token_budget=4_000,
            daily_job_limit=5,
            daily_token_limit=4_000,
        )
        with self.assertRaises(QuotaExceeded):
            self.store.reserve(
                owner_id="owner-a",
                reservation_key="job-2",
                request_hash=digest("request-2"),
                token_budget=4_000,
                daily_job_limit=5,
                daily_token_limit=4_000,
            )

    def test_release_rolls_back_failed_job_reservation(self):
        self.reserve("job-1")
        self.assertTrue(self.store.release(
            owner_id="owner-a", reservation_key="job-1", request_hash=digest("request")
        ))
        self.assertEqual(self.store.get_usage("owner-a")["jobs_reserved"], 0)
        self.assertFalse(self.store.release(
            owner_id="owner-a", reservation_key="job-1", request_hash=digest("request")
        ))

    def test_provider_usage_is_recorded_separately_from_reservation(self):
        self.reserve("job-1")
        record = self.store.record_usage(
            owner_id="owner-a",
            job_id="job-1",
            model="test-model",
            input_tokens=120,
            output_tokens=80,
            cost_micros=37,
        )
        usage = self.store.get_usage("owner-a")
        self.assertEqual(record["total_tokens"], 200)
        self.assertEqual(usage["tokens_reserved"], 4_000)
        self.assertEqual(usage["tokens_actual"], 200)
        self.assertEqual(usage["cost_micros"], 37)
        self.assertEqual(usage["jobs_completed"], 1)


if __name__ == "__main__":
    unittest.main()
