import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TestSprint2Refactoring(unittest.TestCase):
    def test_generation_paths_share_post_pipeline_module(self):
        gen_src = (ROOT / "backend/core/mixins/generation.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(
            gen_src.count("run_post_chapter_pipeline("),
            2,
            "Both standard/batch and atomic generation paths must delegate to run_post_chapter_pipeline",
        )
        self.assertTrue((ROOT / "backend/core/mixins/post_pipeline.py").is_file())

    def test_story_director_modularized_with_backward_compatible_reexports(self):
        rules_path = ROOT / "backend/core/interactive/director_rules.py"
        sd_path = ROOT / "backend/core/interactive/story_director.py"
        self.assertTrue(rules_path.is_file())
        sd_lines = len(sd_path.read_text(encoding="utf-8").splitlines())
        self.assertLess(
            sd_lines,
            2600,
            f"story_director.py should be slimmed below 2600 lines after extraction (got {sd_lines})",
        )
        sd_src = sd_path.read_text(encoding="utf-8")
        for sym in ("compute_present", "promise_ledger_update", "tension_update", "cast_presets_build"):
            self.assertIn(sym, sd_src)

    def test_index_html_inline_styles_significantly_reduced(self):
        idx_src = (ROOT / "web/index.html").read_text(encoding="utf-8")
        inline_count = len(re.findall(r'\sstyle="', idx_src))
        self.assertLess(
            inline_count,
            200,
            f"Expected inline styles in web/index.html to be reduced below 200 (got {inline_count})",
        )

    def test_workshop_removed_dead_xiaohongshu_boot_fetch_and_added_api_helper(self):
        ws_src = (ROOT / "web/assets/workshop.js").read_text(encoding="utf-8")
        self.assertNotIn("fetch('/api/xiaohongshu/templates')", ws_src)
        self.assertIn("function apiJson(", ws_src)


if __name__ == "__main__":
    unittest.main()
