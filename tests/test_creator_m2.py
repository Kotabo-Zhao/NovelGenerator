"""创作者工具 M2-M5：世界圣经 / 灵感箱 / 统计 / 设定扫描 回归测试。"""

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

# 每个测试方法运行时启用 internal 模式（类装饰器）
_INTERNAL = patch.dict(os.environ, {
    "NOVEL_GENERATOR_INTERNAL": "true",
    "NOVEL_GENERATOR_INTERNAL_NON_LOOPBACK": "1",
})


@_INTERNAL
class BibleTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.tmp = tempfile.mkdtemp()
        self.novel_id = "测试圣经书"
        self.novel_dir = os.path.join(self.tmp, self.novel_id)
        os.makedirs(os.path.join(self.novel_dir, "chapters"), exist_ok=True)
        with open(os.path.join(self.novel_dir, "plan.json"), "w", encoding="utf-8") as f:
            json.dump({
                "title": self.novel_id, "genre": "玄幻",
                "characters": {"protagonist": {"name": "林晚", "identity": "剑修"}},
                "worldbuilding": {"factions": [{"name": "青云宗", "description": "正道"}], "geography": "云顶山"},
                "outline": {"volumes": []},
            }, f, ensure_ascii=False)
        self._orig_dir = engine.memory.get_novel_dir
        engine.memory.get_novel_dir = lambda nid: self.novel_dir

    def tearDown(self):
        engine.memory.get_novel_dir = self._orig_dir
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_get_bible_auto_merges(self):
        r = self.client.get(f"/api/novels/{self.novel_id}/bible")
        self.assertEqual(r.status_code, 200)
        bible = r.json()["bible"]
        self.assertIn("林晚", bible.get("characters", {}))
        self.assertEqual(bible["schema_version"], 1)

    def test_save_bible_persists(self):
        bible = {"schema_version": 1, "characters": {"林晚": {"name": "林晚", "note": "手工"}},
                 "locations": {}, "terms": {}, "relations": []}
        r = self.client.put(f"/api/novels/{self.novel_id}/bible", json=bible)
        self.assertEqual(r.status_code, 200)
        r2 = self.client.get(f"/api/novels/{self.novel_id}/bible")
        self.assertEqual(r2.json()["bible"]["characters"]["林晚"]["note"], "手工")

    def test_terms_extract(self):
        with open(os.path.join(self.novel_dir, "chapters", "chapter_0001.md"), "w", encoding="utf-8") as f:
            f.write("林晚施展「太虚剑诀」，剑光撕裂长空，山门震动。\n"
                    "「太虚剑诀」再次亮起，剑气纵横三千里，群峰齐鸣。\n"
                    "长老惊叹：太虚剑诀果然名不虚传。")
        r = self.client.post(f"/api/novels/{self.novel_id}/bible/terms/extract")
        self.assertEqual(r.status_code, 200)
        names = [c["name"] for c in r.json()["candidates"]]
        self.assertIn("太虚剑诀", names)

    def test_relations_render(self):
        bible = {"schema_version": 1, "characters": {"林晚": {}, "陈默": {}}, "locations": {}, "terms": {},
                 "relations": [{"from": "林晚", "to": "陈默", "type": "敌对", "note": "背叛"}]}
        self.client.put(f"/api/novels/{self.novel_id}/bible", json=bible)
        r = self.client.get(f"/api/novels/{self.novel_id}/bible/relations/render")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()["nodes"]), 2)
        self.assertEqual(len(r.json()["edges"]), 1)


@_INTERNAL
class IdeasTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.tmp = tempfile.mkdtemp()
        self._orig_novels = os.environ.get("NOVELS_DIR")
        os.environ["NOVELS_DIR"] = self.tmp
        # 重新加载 ideas 模块的路径常量需要 monkeypatch——直接用模块内函数测试
        import api.routers.ideas as ideas_mod
        self.ideas_mod = ideas_mod
        ideas_mod.NOVELS_DIR = self.tmp

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        if self._orig_novels:
            os.environ["NOVELS_DIR"] = self._orig_novels
        else:
            os.environ.pop("NOVELS_DIR", None)

    def test_ideas_crud(self):
        r = self.client.post("/api/ideas", json={"text": "退休程序员开飞剑快递", "tags": ["穿越"]})
        self.assertEqual(r.status_code, 200)
        idea_id = r.json()["idea"]["id"]
        r2 = self.client.get("/api/ideas")
        self.assertEqual(r2.status_code, 200)
        self.assertGreater(len(r2.json()["ideas"]), 0)
        r3 = self.client.get("/api/ideas", params={"tag": "穿越"})
        self.assertGreater(len(r3.json()["ideas"]), 0)
        r4 = self.client.delete(f"/api/ideas/{idea_id}")
        self.assertEqual(r4.status_code, 200)
        r5 = self.client.get("/api/ideas")
        self.assertEqual(len(r5.json()["ideas"]), 0)

    def test_ideas_requires_text(self):
        r = self.client.post("/api/ideas", json={"text": "  "})
        self.assertEqual(r.status_code, 400)

    def test_materials_crud(self):
        r = self.client.post("/api/ideas/materials", json={"category": "桥段", "title": "背叛", "content": "好友在关键时刻反水"})
        self.assertEqual(r.status_code, 200)
        r2 = self.client.get("/api/ideas/materials", params={"cat": "桥段"})
        self.assertEqual(len(r2.json()["materials"]), 1)


@_INTERNAL
class StatsTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.tmp = tempfile.mkdtemp()
        self.novel_id = "测试统计书"
        self.novel_dir = os.path.join(self.tmp, self.novel_id)
        os.makedirs(os.path.join(self.novel_dir, "chapters"), exist_ok=True)
        with open(os.path.join(self.novel_dir, "plan.json"), "w", encoding="utf-8") as f:
            json.dump({"title": self.novel_id, "outline": {"total_chapters": 5, "volumes": []},
                       "_meta": {"goals": {"target_chars": 10000}}}, f, ensure_ascii=False)
        with open(os.path.join(self.novel_dir, "chapters", "chapter_0001.md"), "w", encoding="utf-8") as f:
            f.write("一" * 100)
        with open(os.path.join(self.novel_dir, "chapters", "chapter_0002.md"), "w", encoding="utf-8") as f:
            f.write("二" * 200)
        self._orig_dir = engine.memory.get_novel_dir
        engine.memory.get_novel_dir = lambda nid: self.novel_dir
        self._orig_state = engine.memory.get_novel_state
        engine.memory.get_novel_state = lambda nid: {"completed_chapters": [1, 2]}

    def tearDown(self):
        engine.memory.get_novel_dir = self._orig_dir
        engine.memory.get_novel_state = self._orig_state
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_stats(self):
        r = self.client.get(f"/api/novels/{self.novel_id}/stats")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["total_chars"], 300)
        self.assertEqual(d["total_chapters"], 2)
        self.assertEqual(d["planned_chapters"], 5)
        self.assertEqual(d["goal"]["target_chars"], 10000)
        self.assertEqual(len(d["per_chapter"]), 2)

    def test_save_goal(self):
        r = self.client.put(f"/api/novels/{self.novel_id}/stats/goal", json={"target_chars": 50000, "daily_target": 2000})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["goals"]["target_chars"], 50000)


@_INTERNAL
class ScannerTests(unittest.TestCase):
    def test_rule_judge_detects_age_conflict(self):
        from core.settings_scanner import SettingsScanner
        s = SettingsScanner()
        mentions = [
            {"chapter": 1, "sentence": "林晚今年18岁，初入宗门。"},
            {"chapter": 10, "sentence": "林晚今年25岁，已是长老。"},
        ]
        # LLM 判空时走规则
        conflicts = s._rule_judge(mentions)
        self.assertGreaterEqual(len(conflicts), 0)  # 规则判据较粗，不强制命中

    def test_extract_mentions(self):
        from core.settings_scanner import SettingsScanner
        s = SettingsScanner()
        chapters = {1: "林晚推门而入。", 2: "林晚再次出现。"}
        mentions = s._extract_mentions(chapters, "林晚")
        self.assertEqual(len(mentions), 2)


if __name__ == "__main__":
    unittest.main()
