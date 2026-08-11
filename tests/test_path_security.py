"""路径安全回归测试（不依赖 FastAPI/OpenAI，可在最小 Python 环境执行）。"""
import os
import tempfile
import unittest

from pathlib import Path

import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from core.path_security import safe_novel_dir, safe_novel_file, validate_novel_id
from core.shared_memory import SharedMemoryManager
from core.interactive.interact_store import InteractStore


class PathSecurityTests(unittest.TestCase):
    def test_accepts_unicode_novel_id(self):
        self.assertEqual(validate_novel_id("逆天废材：从悬崖开始"), "逆天废材：从悬崖开始")

    def test_rejects_path_separators_and_traversal(self):
        for novel_id in ("..", "../outside", r"..\outside", r"C:\outside", "a/b", "a\\b"):
            with self.subTest(novel_id=novel_id):
                with self.assertRaises(ValueError):
                    validate_novel_id(novel_id)

    def test_rejects_windows_reserved_names(self):
        for novel_id in ("CON", "NUL", "COM1", "LPT9", "con.txt"):
            with self.subTest(novel_id=novel_id):
                with self.assertRaises(ValueError):
                    validate_novel_id(novel_id)

    def test_safe_novel_dir_stays_under_root(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(safe_novel_dir(root, "测试作品"))
            self.assertEqual(path.parent, Path(root).resolve())
            self.assertEqual(path.name, "测试作品")

    def test_safe_novel_file_allows_internal_relative_path(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(safe_novel_file(root, "测试作品", "chapters/chapter_0001.md"))
            self.assertEqual(path.parent.name, "chapters")
            self.assertTrue(str(path).startswith(str(Path(root).resolve())))

    def test_safe_novel_file_rejects_escape(self):
        with tempfile.TemporaryDirectory() as root:
            for relative in ("../secret.txt", r"..\secret.txt", r"C:\secret.txt"):
                with self.subTest(relative=relative):
                    with self.assertRaises(ValueError):
                        safe_novel_file(root, "测试作品", relative)

    def test_shared_memory_rejects_unsafe_id_before_io(self):
        with tempfile.TemporaryDirectory() as root:
            memory = SharedMemoryManager(root)
            with self.assertRaises(ValueError):
                memory.read("plan", "../outside")

    def test_interactive_store_rejects_unsafe_id_before_io(self):
        with tempfile.TemporaryDirectory() as root:
            store = InteractStore(root)
            with self.assertRaises(ValueError):
                store.exists(r"..\outside")

    def test_symlinked_novel_dir_cannot_escape_root(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            link = Path(root) / "linked"
            try:
                os.symlink(outside, link, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("当前 Windows 环境不允许创建目录符号链接")
            with self.assertRaises(ValueError):
                safe_novel_dir(root, "linked")


if __name__ == "__main__":
    unittest.main()
