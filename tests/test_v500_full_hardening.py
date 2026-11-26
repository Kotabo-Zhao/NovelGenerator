import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TestV500FullHardening(unittest.TestCase):
    def test_zero_utf8_bom_across_backend_python_files(self):
        bom_files = []
        for p in (ROOT / "backend").rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            raw = p.read_bytes()
            if raw.startswith(b"\xef\xbb\xbf"):
                bom_files.append(str(p.relative_to(ROOT)))
        self.assertEqual(bom_files, [], f"UTF-8 BOM found in: {bom_files}")

    def test_workshop_js_has_zero_dead_xiaohongshu_code(self):
        ws = (ROOT / "web/assets/workshop.js").read_text(encoding="utf-8")
        self.assertNotIn("/api/xiaohongshu/", ws)
        self.assertNotIn("doXhsCreate", ws)
        self.assertNotIn("xhsTemplates", ws)
        self.assertNotIn("xhsMode", ws)

    def test_all_four_modes_aligned_with_human_voice_and_anti_repetition(self):
        gen = (ROOT / "backend/core/mixins/generation.py").read_text(encoding="utf-8")
        a_start = gen.index("async def atomic_generate_chapter_stream(")
        s_start = gen.index("async def generate_chapter_stream(")
        atomic_body = gen[a_start:s_start]
        self.assertIn("build_rolling_cliche_block", atomic_body)
        self.assertIn("polish_flagged_paragraphs", atomic_body)

        sd = (ROOT / "backend/core/interactive/story_director.py").read_text(encoding="utf-8")
        de = (ROOT / "backend/core/interactive/dialogue_engine.py").read_text(encoding="utf-8")
        self.assertIn("frequency_penalty", sd)
        self.assertIn("frequency_penalty", de)

    def test_android_cleartext_scoped_to_loopback_via_network_security_config(self):
        nsc = ROOT / "android/app/src/main/res/xml/network_security_config.xml"
        self.assertTrue(nsc.is_file())
        nsc_txt = nsc.read_text(encoding="utf-8")
        self.assertIn('cleartextTrafficPermitted="false"', nsc_txt)
        self.assertIn("127.0.0.1", nsc_txt)
        manifest = (ROOT / "android/app/src/main/AndroidManifest.xml").read_text(encoding="utf-8")
        self.assertIn('android:networkSecurityConfig="@xml/network_security_config"', manifest)
        self.assertNotIn('android:usesCleartextTraffic="true"', manifest)

    def test_core_hotspots_reduced_silent_except_pass(self):
        for rel in (
            "backend/core/interactive/story_director.py",
            "backend/core/mixins/generation.py",
            "backend/core/interactive/interact_store.py",
            "backend/core/shared_memory.py",
        ):
            src = (ROOT / rel).read_text(encoding="utf-8")
            tree = ast.parse(src)
            silent = sum(
                1
                for n in ast.walk(tree)
                if isinstance(n, ast.ExceptHandler)
                and len(n.body) == 1
                and isinstance(n.body[0], ast.Pass)
                and isinstance(n.type, ast.Name)
                and n.type.id == "Exception"
            )
            self.assertEqual(silent, 0, f"{rel} still has {silent} silent `except Exception: pass`")


if __name__ == "__main__":
    unittest.main()
