"""Regression tests for writing capability & workshop UX optimizations."""

import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from core.ai_detector import AIDetector
from core.context_budget import ContextSection, allocate_context
from core.quality_gate import validate_chapter_text
from core.writer import _check_truncation, _dedup_continuation, _trim_to_target


class WritingAndUXOptimizationTests(unittest.TestCase):
    def test_context_budget_preserves_required_floor_and_cjk_boundaries(self):
        huge_req = "第一段核心背景设定，包含重要人物顾尘的身世。" * 30
        second_req = "本章大纲：顾尘在雨夜码头截住走私船，夺回账本。"
        optional_bg = "次要背景信息。" * 40
        rendered, trimmed = allocate_context(
            [
                ContextSection("世界观", huge_req, priority=10, required=True),
                ContextSection("本章大纲", second_req, priority=9, required=True),
                ContextSection("参考资料", optional_bg, priority=1, required=False),
            ],
            max_chars=300,
        )
        self.assertIn("本章大纲", rendered)
        self.assertIn("夺回账本", rendered)
        self.assertIn("参考资料", trimmed)

    def test_dedup_continuation_handles_leading_newlines_and_echo(self):
        existing = "雨点砸在铁皮棚上，发出密集的脆响。他握紧了伞柄。"
        cont = "\n\n他握紧了伞柄。转身走向巷口。"
        deduped = _dedup_continuation(existing, cont)
        self.assertEqual(deduped, "转身走向巷口。")

    def test_check_truncation_accepts_chinese_closing_quotes(self):
        text = "雨夜的码头空无一人，只有潮水拍打栈桥。" * 15 + "他低声说：“东西我带走了。”"
        truncated, reason = _check_truncation(text, target_words=400)
        self.assertFalse(truncated, reason)

    def test_trim_to_target_preserves_balanced_delimiters(self):
        # Construct a short-target chapter where naive slicing at target_words splits inside 「...」
        prefix = "夜风穿过长街，灯笼在檐下摇晃。" * 12  # ~180 chars
        dialogue = "「你以为今天还能全身而退？门外的人早就围上来了。把账本交出来，我饶你一命。」"
        suffix = "他没有回答，只是把袖口往下拉了拉。" * 12
        full = prefix + dialogue + suffix
        trimmed = _trim_to_target(full, target_words=210)
        # Must pass deterministic balanced-delimiter quality gate
        res = validate_chapter_text(trimmed, target_words=210)
        self.assertTrue(res["delimiters_balanced"])

    def test_analyze_prose_craft_detects_cliches_and_builds_revision_prompt(self):
        sample = (
            "他感到心中充满愤怒，嘴角勾起一抹冷笑，眼中闪过一丝寒光。"
            "与此同时，她意识到事情并不简单，深吸一口气，缓缓开口说道。"
            "这不仅仅是一次考验，更标志着他内心的彻底蜕变。"
        ) * 5
        report = AIDetector.analyze_prose_craft(sample)
        self.assertTrue(report["offline"])
        self.assertGreater(report["ai_score"], 40)
        self.assertLess(report["craft_score"], 60)
        self.assertTrue(report["flagged_sections"])
        self.assertTrue(report["suggestions"])
        self.assertTrue(report["suggested_revision_prompt"])

    def test_frontend_speak_button_unlocked_and_craft_ux_wired(self):
        html = (REPO / "web" / "index.html").read_text(encoding="utf-8")
        js = (REPO / "web" / "assets" / "workshop.js").read_text(encoding="utf-8")
        self.assertNotIn('@click="speakChapter" :disabled="speaking"', html)
        self.assertIn("runCraftCheck", html)
        self.assertIn("applyCraftRevisionPrompt", html)
        self.assertIn("toggleZenMode", html)
        self.assertIn("chapterWordCount", html)
        self.assertIn("runCraftCheck,applyCraftRevisionPrompt", js)


if __name__ == "__main__":
    unittest.main()
