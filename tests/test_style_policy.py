"""Compliance regression tests for attribute-based style presets."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from core.styles import build_style_prompt, get_style, get_style_categories


class StylePolicyTests(unittest.TestCase):
    def test_prompt_uses_attributes_not_author_attribution(self):
        style = get_style("唐三风格")
        prompt = build_style_prompt(style)

        self.assertNotIn("作者定位", prompt)
        self.assertNotIn("唐家三少", prompt)
        self.assertNotIn("土豆方法论", prompt)
        self.assertIn("文笔特征", prompt)
        self.assertIn("节奏控制", prompt)

    def test_legacy_style_name_maps_to_generic_display_name(self):
        legacy = get_style("唐三风格")
        generic = get_style("团队成长")

        self.assertEqual(legacy["name"], "团队成长")
        self.assertEqual(generic["name"], "团队成长")
        self.assertNotIn("author", legacy)

    def test_categories_do_not_expose_author_style_group(self):
        categories = get_style_categories()

        self.assertNotIn("男频·作者风格", categories)
        self.assertNotIn("女频·作者风格", categories)
        self.assertIn("团队成长", sum(categories.values(), []))


if __name__ == "__main__":
    unittest.main()
