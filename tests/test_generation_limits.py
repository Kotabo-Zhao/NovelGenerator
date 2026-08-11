"""生成 token 上限回归测试。"""
import unittest

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from core.generation_limits import max_generation_tokens


class GenerationLimitTests(unittest.TestCase):
    def test_normal_chapter_is_capped_by_hard_limit(self):
        self.assertEqual(max_generation_tokens(1500, hard_cap=4000), 4000)

    def test_continuation_multiplier_cannot_bypass_hard_limit(self):
        self.assertEqual(max_generation_tokens(2000, multiplier=6, hard_cap=4000), 4000)

    def test_small_chapter_keeps_a_reasonable_minimum(self):
        self.assertEqual(max_generation_tokens(100, hard_cap=4000), 300)

    def test_invalid_values_fail_closed(self):
        with self.assertRaises(ValueError):
            max_generation_tokens(0)
        with self.assertRaises(ValueError):
            max_generation_tokens(1000, hard_cap=0)


if __name__ == "__main__":
    unittest.main()
