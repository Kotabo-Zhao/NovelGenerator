"""创作者工具 M1：全文检索 / 章节精细操作 / 版本历史 回归测试。"""

import importlib.util
import importlib.machinery
import json
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

if importlib.util.find_spec("openai") is None:
    fake_openai = types.ModuleType("openai")
    fake_openai.__spec__ = importlib.machinery.ModuleSpec("openai", loader=None)

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

    fake_openai.OpenAI = FakeClient
    for _name in ("APIError", "RateLimitError", "APITimeoutError", "APIConnectionError"):
        setattr(fake_openai, _name, type(_name, (Exception,), {}))
    sys.modules["openai"] = fake_openai
if importlib.util.find_spec("dotenv") is None:
    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.__spec__ = importlib.machinery.ModuleSpec("dotenv", loader=None)
    fake_dotenv.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = fake_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from fastapi.testclient import TestClient
from api.server import app
from api.deps import engine

# 每个测试方法运行时启用 internal 模式（类装饰器，不依赖 setUp 链，不污染全局）
_INTERNAL = patch.dict(os.environ, {
    "NOVEL_GENERATOR_INTERNAL": "true",
    "NOVEL_GENERATOR_INTERNAL_NON_LOOPBACK": "1",
})


@_INTERNAL
class SearchTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        # 造一本临时小说
        self.tmp = tempfile.mkdtemp()
        self.novel_id = "测试检索书"
        self.novel_dir = os.path.join(self.tmp, self.novel_id)
        os.makedirs(os.path.join(self.novel_dir, "chapters"), exist_ok=True)
        with open(os.path.join(self.novel_dir, "plan.json"), "w", encoding="utf-8") as f:
            json.dump({
                "title": self.novel_id, "genre": "玄幻", "style": "热血",
                "outline": {"volumes": [{"chapters": [
                    {"number": 1, "title": "第一章", "summary": "主角觉醒青铜剑", "hook": "剑鸣", "opening_scene": "山中"},
                ]}]},
            }, f, ensure_ascii=False)
        with open(os.path.join(self.novel_dir, "chapters", "chapter_0001.md"), "w", encoding="utf-8") as f:
            f.write("林晚推开木门，青铜剑在月光下发出低鸣。她握紧剑柄。\n\n继续赶路。")
        with open(os.path.join(self.novel_dir, "chapters", "chapter_0002.md"), "w", encoding="utf-8") as f:
            f.write("深夜，青铜剑再次震动，指向北方。")
        with open(os.path.join(self.novel_dir, "character_bible.json"), "w", encoding="utf-8") as f:
            json.dump({"林晚": {"identity": "剑修", "personality": "坚韧"}}, f, ensure_ascii=False)
        self._orig_dir = engine.memory.get_novel_dir
        engine.memory.get_novel_dir = lambda nid: self.novel_dir

    def tearDown(self):
        engine.memory.get_novel_dir = self._orig_dir
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _search(self, q, limit=50):
        return self.client.get(f"/api/novels/{self.novel_id}/search", params={"q": q, "limit": limit})

    def test_search_chapter_hits(self):
        r = self._search("青铜剑")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertGreater(d["count"], 0)
        ch_sources = [x for x in d["results"] if x["source"] == "chapter"]
        self.assertGreaterEqual(len(ch_sources), 2)  # ch1 + ch2 都命中

    def test_search_outline_hits(self):
        r = self._search("觉醒")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(any(x["source"] == "outline" for x in r.json()["results"]))

    def test_search_bible_hits(self):
        r = self._search("剑修")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(any(x["source"] == "bible" for x in r.json()["results"]))

    def test_search_no_result(self):
        r = self._search("不存在的词xyz")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["count"], 0)

    def test_search_empty_query_rejected(self):
        r = self._search("  ")
        self.assertEqual(r.status_code, 400)

    def test_search_limit(self):
        r = self._search("剑", limit=1)
        self.assertLessEqual(r.json()["count"], 1)


@_INTERNAL
class SurgicalEditTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.tmp = tempfile.mkdtemp()
        self.novel_id = "测试手术书"
        self.novel_dir = os.path.join(self.tmp, self.novel_id)
        os.makedirs(os.path.join(self.novel_dir, "chapters"), exist_ok=True)
        with open(os.path.join(self.novel_dir, "plan.json"), "w", encoding="utf-8") as f:
            json.dump({"title": self.novel_id, "outline": {"volumes": [{"chapters": [
                {"number": 1, "title": "第一章", "summary": "s", "hook": "h"},
                {"number": 2, "title": "第二章", "summary": "s", "hook": "h"},
            ]}]}}, f, ensure_ascii=False)
        self.ch1 = "林晚推门而入。\n\n\n\n她看见了那把剑。\n\n\n\n剑在颤抖。\n\n\n\n她伸手握住。"
        with open(os.path.join(self.novel_dir, "chapters", "chapter_0001.md"), "w", encoding="utf-8") as f:
            f.write(self.ch1)
        with open(os.path.join(self.novel_dir, "chapters", "chapter_0002.md"), "w", encoding="utf-8") as f:
            f.write("第二章内容。")
        self._orig_dir = engine.memory.get_novel_dir
        engine.memory.get_novel_dir = lambda nid: self.novel_dir

    def tearDown(self):
        engine.memory.get_novel_dir = self._orig_dir
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _surgical(self, op, instruction=None, selection=None, merge_with=None, target_words=None):
        body = {"op": op, "instruction": instruction, "selection": selection, "merge_with": merge_with,
                "target_words": target_words}
        return self.client.post(f"/api/novels/{self.novel_id}/chapters/1/surgical", json=body)

    def test_continue_appends(self):
        fake = MagicMock()
        fake.choices[0].message.content = "新的续写内容。"
        with patch.object(engine.writer, "_create", return_value=fake):
            r = self._surgical("continue", target_words=100)
        self.assertEqual(r.status_code, 200)
        self.assertIn("新的续写内容", r.json()["content"])
        # 原文保留
        self.assertIn("林晚推门而入", r.json()["content"])

    def test_rewrite_needs_instruction(self):
        r = self._surgical("rewrite")
        self.assertEqual(r.status_code, 400)

    def test_rewrite_replaces_selection(self):
        fake = MagicMock()
        fake.choices[0].message.content = "改写后的段落。"
        with patch.object(engine.writer, "_create", return_value=fake):
            r = self._surgical("rewrite", instruction="更紧张", selection={"start": 0, "end": 6})
        self.assertEqual(r.status_code, 200)
        # 选中区被替换
        self.assertNotIn("林晚推门", r.json()["content"])
        self.assertIn("改写后的段落", r.json()["content"])

    def test_split_creates_next_chapter(self):
        r = self._surgical("split")
        self.assertEqual(r.status_code, 200)
        ch2 = os.path.join(self.novel_dir, "chapters", "chapter_0002.md")
        self.assertTrue(os.path.exists(ch2))
        # 原第2章文件应后移为第3章
        ch3 = os.path.join(self.novel_dir, "chapters", "chapter_0003.md")
        self.assertTrue(os.path.exists(ch3))

    def test_versions_created_after_edit(self):
        fake = MagicMock()
        fake.choices[0].message.content = "续写内容。"
        with patch.object(engine.writer, "_create", return_value=fake):
            self._surgical("continue")
        vdir = os.path.join(self.novel_dir, ".versions")
        self.assertTrue(os.path.isdir(vdir))
        r = self.client.get(f"/api/novels/{self.novel_id}/chapters/1/versions")
        self.assertEqual(r.status_code, 200)
        self.assertGreater(len(r.json()["versions"]), 0)

    def test_rollback_restores(self):
        # 先产生版本
        fake = MagicMock()
        fake.choices[0].message.content = "修改后的内容。"
        with patch.object(engine.writer, "_create", return_value=fake):
            self._surgical("continue")
        # 回滚到 v1（原始）
        r = self.client.post(
            f"/api/novels/{self.novel_id}/chapters/1/versions/rollback",
            json={"to_version": 1},
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn("林晚推门而入", r.json()["content"])

    def test_merge_combines(self):
        r = self._surgical("merge", merge_with=2)
        self.assertEqual(r.status_code, 200)
        self.assertIn("第二章内容", r.json()["content"])
        self.assertFalse(os.path.exists(os.path.join(self.novel_dir, "chapters", "chapter_0002.md")))


if __name__ == "__main__":
    unittest.main()
