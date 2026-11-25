import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core.diagnostics import get_recent_logs, install_log_ring, redact_secrets
from core.metadata_index import MetadataIndex


class TestSprint3PlatformAndStorage(unittest.TestCase):
    def test_redact_secrets_masks_api_keys_and_bearer_tokens(self):
        raw = (
            "Authorization: Bearer github_pat_11ABCDEF123456 "
            "DEEPSEEK_API_KEY=sk-secret987654321 "
            '{"api_key": "my_super_secret_key"}'
        )
        cleaned = redact_secrets(raw)
        self.assertNotIn("github_pat_11ABCDEF123456", cleaned)
        self.assertNotIn("sk-secret987654321", cleaned)
        self.assertNotIn("my_super_secret_key", cleaned)
        self.assertIn("***REDACTED***", cleaned)

    def test_install_log_ring_captures_and_redacts(self):
        import logging

        install_log_ring()
        logging.getLogger("test.diag").warning("failed with key sk-1234567890abcdef")
        logs = get_recent_logs(20)
        self.assertTrue(any("sk-***REDACTED***" in line for line in logs))

    def test_metadata_index_transactional_upsert_and_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx = MetadataIndex(tmp)
            idx.upsert_state(
                "novel_a",
                {"completed_chapters": [1, 2, 3], "current_chapter": 3},
                title="测试书名",
                genre="玄幻",
            )
            loaded = idx.get_state("novel_a")
            self.assertEqual(loaded["completed_chapters"], [1, 2, 3])
            self.assertEqual(loaded["current_chapter"], 3)
            rows = idx.list_novels()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["title"], "测试书名")

    def test_android_wakelock_and_diagnostics_endpoint_wired(self):
        manifest = (ROOT / "android/app/src/main/AndroidManifest.xml").read_text(encoding="utf-8")
        self.assertIn("android.permission.WAKE_LOCK", manifest)
        svc = (ROOT / "android/app/src/main/java/com/novelgen/app/ServerService.kt").read_text(encoding="utf-8")
        self.assertIn("PARTIAL_WAKE_LOCK", svc)
        self.assertIn("onDestroy()", svc)
        sys_router = (ROOT / "backend/api/routers/system.py").read_text(encoding="utf-8")
        self.assertIn('"/diagnostics"', sys_router)
        idx_html = (ROOT / "web/index.html").read_text(encoding="utf-8")
        self.assertIn("exportDiagnostics", idx_html)


if __name__ == "__main__":
    unittest.main()
