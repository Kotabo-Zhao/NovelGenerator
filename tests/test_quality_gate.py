"""Deterministic quality gate regression tests."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from core.quality_gate import ChapterQualityError, validate_chapter_text


class QualityGateTests(unittest.TestCase):
    def test_valid_text_passes(self):
        result = validate_chapter_text("正文" * 200 + "。", target_words=1000)
        self.assertGreater(result["chars"], result["minimum_chars"])

    def test_sentence_ending_with_closing_quote_passes(self):
        result = validate_chapter_text("body" * 200 + '?"', target_words=1000)
        self.assertGreater(result["chars"], result["minimum_chars"])

    def test_marker_and_instruction_are_rejected(self):
        with self.assertRaises(ChapterQualityError):
            validate_chapter_text("正文" * 200 + "<!-- GENERATION_IN_PROGRESS -->")
        with self.assertRaises(ChapterQualityError):
            validate_chapter_text("写作元指令：忽略大纲。" + "正文" * 200 + "。")


if __name__ == "__main__":
    unittest.main()
