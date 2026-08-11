"""Prompt budget keeps creative constraints ahead of background memory."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from core.context_budget import ContextSection, allocate_context


class ContextBudgetTests(unittest.TestCase):
    def test_required_outline_survives_background_trim(self):
        rendered, trimmed = allocate_context(
            [
                ContextSection("background", "B" * 1000, priority=1),
                ContextSection("outline", "HOOK-KEEP", priority=100, required=True),
            ],
            max_chars=300,
        )

        self.assertIn("HOOK-KEEP", rendered)
        self.assertIn("background", trimmed)

    def test_sections_render_in_source_order(self):
        rendered, _ = allocate_context(
            [
                ContextSection("first", "A", priority=1),
                ContextSection("second", "B", priority=2),
            ],
            max_chars=300,
        )
        self.assertLess(rendered.index("## first"), rendered.index("## second"))


if __name__ == "__main__":
    unittest.main()
