"""Compliance regression tests for attribute-based style presets."""

import os
import sys
import unittest
from pathlib import Path

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
        self.assertEqual(list(categories), ["推荐叙事取向"])
        self.assertEqual(
            categories["推荐叙事取向"],
            [
                "紧凑推进", "克制现实", "轻松对话", "悬疑冷感",
                "热血升级", "细腻情感", "宏大群像", "日常治愈",
            ],
        )

    def test_public_prototype_keeps_legacy_presets_compatible(self):
        public_style = get_style("紧凑推进")
        legacy_style = get_style("团队成长")

        self.assertEqual(public_style["name"], "紧凑推进")
        self.assertIn("description", public_style)
        self.assertEqual(legacy_style["name"], "团队成长")

    def test_creation_ui_removes_duplicate_parameterized_style_entry(self):
        source = (Path(__file__).parents[1] / "web" / "index.html").read_text(encoding="utf-8")

        self.assertNotIn("styleMode==='params'", source)
        self.assertNotIn("loadStyleParams", source)
        self.assertNotIn("applyParamStyle", source)
        self.assertNotIn("sc-author", source)
        self.assertIn("叙事取向", source)


if __name__ == "__main__":
    unittest.main()
