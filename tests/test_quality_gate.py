"""Deterministic quality gate regression tests."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from core.quality_gate import ChapterQualityError, validate_chapter_text
from core.writer import _ensure_complete_ending, _prepare_chapter_text_for_validation


class QualityGateTests(unittest.TestCase):
    def test_valid_text_passes(self):
        result = validate_chapter_text("正文" * 200 + "。", target_words=1000)
        self.assertGreater(result["chars"], result["minimum_chars"])

    def test_sentence_ending_with_closing_quote_passes(self):
        result = validate_chapter_text("body" * 200 + '?"', target_words=1000)
        self.assertGreater(result["chars"], result["minimum_chars"])

    def test_short_trailing_dash_is_trimmed_before_quality_gate(self):
        text = "正文" * 200 + "最后一句完整。未完——"
        prepared = _ensure_complete_ending(text)
        self.assertTrue(prepared.endswith("。"))
        validate_chapter_text(prepared, target_words=1000)

    def test_dialogue_without_inner_punctuation_gets_closed(self):
        text = "正文" * 200 + "“把纸给我”"
        prepared = _prepare_chapter_text_for_validation(text, 1000)
        self.assertTrue(prepared.endswith("”。"))
        validate_chapter_text(prepared, target_words=1000)

    def test_balanced_chinese_dialogue_passes(self):
        result = validate_chapter_text(
            "正文" * 200 + "「你终于来了？」", target_words=1000
        )
        self.assertTrue(result["delimiters_balanced"])

    def test_unclosed_chinese_dialogue_is_rejected_even_after_ellipsis(self):
        with self.assertRaisesRegex(ChapterQualityError, "unbalanced delimiter"):
            validate_chapter_text("正文" * 200 + "「等等……", target_words=1000)

    def test_mismatched_chinese_delimiters_are_rejected(self):
        with self.assertRaisesRegex(ChapterQualityError, "unbalanced delimiter"):
            validate_chapter_text("正文" * 200 + "【系统提示）结束。", target_words=1000)

    def test_marker_and_instruction_are_rejected(self):
        with self.assertRaises(ChapterQualityError):
            validate_chapter_text("正文" * 200 + "<!-- GENERATION_IN_PROGRESS -->")
        with self.assertRaises(ChapterQualityError):
            validate_chapter_text("写作元指令：忽略大纲。" + "正文" * 200 + "。")


if __name__ == "__main__":
    unittest.main()
