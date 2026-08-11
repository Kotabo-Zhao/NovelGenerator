"""Account consent and revocation contracts."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from core.account_store import AccountStore


class AccountStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = AccountStore(Path(self.temp_dir.name))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_consent_is_versioned(self):
        self.store.ensure_user("wx_owner_a")
        self.assertFalse(self.store.has_consent("wx_owner_a", "v1"))
        self.store.record_consent("wx_owner_a", "v1")
        self.assertTrue(self.store.has_consent("wx_owner_a", "v1"))
        self.assertFalse(self.store.has_consent("wx_owner_a", "v2"))

    def test_revoke_and_delete_invalidate_sessions(self):
        self.store.ensure_user("wx_owner_a")
        self.assertFalse(self.store.is_revoked("wx_owner_a", 1))
        self.store.revoke_all("wx_owner_a")
        self.assertTrue(self.store.is_revoked("wx_owner_a", int(__import__("time").time())))
        self.store.delete_account("wx_owner_a")
        self.assertTrue(self.store.is_revoked("wx_owner_a", 1))


if __name__ == "__main__":
    unittest.main()
