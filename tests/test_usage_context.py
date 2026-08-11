"""Provider usage extraction remains independent from the persistence layer."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from core.usage_context import report_response_usage, usage_scope


class UsageContextTests(unittest.TestCase):
    def test_response_usage_is_forwarded_to_the_current_job_scope(self):
        events = []
        response = SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=12, completion_tokens=8, total_tokens=20)
        )
        with usage_scope(events.append):
            report_response_usage(response, model="demo")
        self.assertEqual(events[0]["total_tokens"], 20)
        self.assertEqual(events[0]["model"], "demo")

    def test_missing_provider_usage_is_not_fabricated(self):
        events = []
        with usage_scope(events.append):
            report_response_usage(SimpleNamespace(usage=None), model="demo")
        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
