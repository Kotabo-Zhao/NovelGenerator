"""Ownership isolation tests for secure novel APIs."""

import os
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from core.ownership_store import OwnershipError, OwnershipStore


class OwnershipStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = OwnershipStore(Path(self.temp_dir.name))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_claim_is_idempotent_for_the_same_owner(self):
        first = self.store.claim("novel-1", "wx_owner_a")
        second = self.store.claim("novel-1", "wx_owner_a")

        self.assertEqual(first["novel_id"], "novel-1")
        self.assertEqual(first["owner_id"], "wx_owner_a")
        self.assertTrue(first["public_id"].startswith("n_"))
        self.assertEqual(first["novel_id"], second["novel_id"])
        self.assertEqual(first["public_id"], second["public_id"])
        self.assertTrue(self.store.owns("novel-1", "wx_owner_a"))
        self.assertTrue(self.store.owns(first["public_id"], "wx_owner_a"))

    def test_cross_owner_claim_and_access_are_rejected(self):
        self.store.claim("novel-1", "wx_owner_a")

        with self.assertRaises(OwnershipError):
            self.store.claim("novel-1", "wx_owner_b")
        with self.assertRaises(OwnershipError):
            self.store.require_owner("novel-1", "wx_owner_b")
        self.assertFalse(self.store.owns("novel-1", "wx_owner_b"))

    def test_list_is_scoped_and_delete_hides_the_record(self):
        self.store.claim("novel-1", "wx_owner_a")
        self.store.claim("novel-2", "wx_owner_a")
        self.store.claim("novel-3", "wx_owner_b")

        self.assertEqual(
            [item["novel_id"] for item in self.store.list_for_owner("wx_owner_a")],
            ["novel-1", "novel-2"],
        )
        self.store.mark_deleted("novel-1", "wx_owner_a")
        self.assertEqual(
            [item["novel_id"] for item in self.store.list_for_owner("wx_owner_a")],
            ["novel-2"],
        )

        with self.assertRaises(OwnershipError):
            self.store.claim("novel-1", "wx_owner_b")

    def test_invalid_ids_and_owners_fail_closed(self):
        with self.assertRaises(ValueError):
            self.store.claim("../escape", "wx_owner_a")
        with self.assertRaises(ValueError):
            self.store.claim("novel-1", "")

    def test_a_second_store_instance_reads_the_same_owner_record(self):
        self.store.claim("novel-1", "wx_owner_a")
        second_store = OwnershipStore(Path(self.temp_dir.name))

        self.assertTrue(second_store.owns("novel-1", "wx_owner_a"))

    def test_creation_idempotency_is_durable_and_request_bound(self):
        request_hash = hashlib.sha256(b"request-a").hexdigest()
        first = self.store.reserve_creation("wx_owner_a", "create-001", request_hash)
        second = self.store.reserve_creation("wx_owner_a", "create-001", request_hash)

        self.assertEqual(first["status"], "running")
        self.assertEqual(first["request_hash"], second["request_hash"])
        with self.assertRaises(ValueError):
            self.store.reserve_creation(
                "wx_owner_a",
                "create-001",
                hashlib.sha256(b"request-b").hexdigest(),
            )

        completed = self.store.complete_creation(
            "wx_owner_a", "create-001", request_hash, "novel-1"
        )
        replay = self.store.reserve_creation("wx_owner_a", "create-001", request_hash)
        self.assertEqual(completed["status"], "succeeded")
        self.assertEqual(replay["novel_id"], "novel-1")


if __name__ == "__main__":
    unittest.main()
