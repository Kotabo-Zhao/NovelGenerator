import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TestSprint1Optimizations(unittest.TestCase):
    def test_version_single_source_of_truth(self):
        ver_src = (ROOT / "backend/core/version.py").read_text(encoding="utf-8")
        m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', ver_src)
        self.assertIsNotNone(m)
        version = m.group(1)

        server_src = (ROOT / "backend/api/server.py").read_text(encoding="utf-8")
        self.assertIn("APP_VERSION", server_src)
        self.assertNotIn('"app_version": "4.3.3"', server_src)

        gradle_src = (ROOT / "android/app/build.gradle.kts").read_text(encoding="utf-8")
        self.assertIn(f'versionName = "{version}"', gradle_src)

    def test_trend_analyzer_has_no_direct_sync_llm_in_async(self):
        src = (ROOT / "backend/core/trend_analyzer.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        direct_calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef):
                for child in ast.walk(node):
                    if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                        if child.func.attr == "create":
                            seg = ast.get_source_segment(src, child) or ""
                            if "completions.create(" in seg:
                                direct_calls.append((node.name, child.lineno))
        self.assertEqual(
            direct_calls,
            [],
            f"Found direct sync completions.create inside async def: {direct_calls}",
        )

    def test_atomic_stream_aligned_with_unified_post_processor_and_craft_report(self):
        src = (ROOT / "backend/core/mixins/generation.py").read_text(encoding="utf-8")
        a_start = src.index("async def atomic_generate_chapter_stream(")
        s_start = src.index("async def generate_chapter_stream(")
        atomic_body = src[a_start:s_start]
        self.assertIn("UnifiedPostProcessor", atomic_body)
        self.assertIn("analyze_prose_craft", atomic_body)
        self.assertIn('"type": "craft_report"', atomic_body)

    def test_prepare_frontend_supports_check_and_is_wired_in_ci(self):
        ci_src = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("prepare_frontend.py --check", ci_src)


if __name__ == "__main__":
    unittest.main()
