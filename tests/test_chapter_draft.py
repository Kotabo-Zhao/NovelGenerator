"""Drafts are isolated from published chapter files and support pagination callers."""

import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from core.shared_memory import SharedMemoryManager


class ChapterDraftTests(unittest.TestCase):
    def test_draft_round_trip_does_not_change_published_chapter(self):
        with tempfile.TemporaryDirectory() as root:
            memory = SharedMemoryManager(root)
            memory.write_chapter("novel-1", 1, "published")
            memory.write_chapter_draft("novel-1", 1, "draft revision")
            self.assertEqual(memory.read_chapter("novel-1", 1), "published")
            self.assertEqual(memory.read_chapter_draft("novel-1", 1), "draft revision")
            self.assertTrue(memory.delete_chapter_draft("novel-1", 1))
            self.assertIsNone(memory.read_chapter_draft("novel-1", 1))

    def test_draft_length_is_bounded(self):
        with tempfile.TemporaryDirectory() as root:
            memory = SharedMemoryManager(root)
            with self.assertRaises(ValueError):
                memory.write_chapter_draft("novel-1", 1, "x" * 50_001)


if __name__ == "__main__":
    unittest.main()
