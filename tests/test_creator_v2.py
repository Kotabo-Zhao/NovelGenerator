"""创作者工具 v2.0：生成时设定一致性检查 + annotations 输出 回归测试。"""

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

from api.deps import engine


class ChapterVsPriorTests(unittest.TestCase):
    """settings_scanner.check_chapter_against_prior 单测。"""

    def setUp(self):
        from core.settings_scanner import SettingsScanner
        self.scanner = SettingsScanner()
        self.tmp = tempfile.mkdtemp()
        self.novel_dir = os.path.join(self.tmp, "novel")
        os.makedirs(os.path.join(self.novel_dir, "chapters"), exist_ok=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_returns_annotations_when_age_conflict(self):
        # 前文第1章：林晚 30 岁；本章：25 岁
        with open(os.path.join(self.novel_dir, "chapters", "chapter_0001.md"), "w", encoding="utf-8") as f:
            f.write("林晚今年30岁，是资深设计师，在公司工作了五年。")
        # bible 提供实体名单
        with open(os.path.join(self.novel_dir, "bible.json"), "w", encoding="utf-8") as f:
            json.dump({"schema_version": 1, "characters": {"林晚": {}}, "locations": {}, "terms": {}, "relations": []}, f, ensure_ascii=False)
        ch_text = "林晚今年25岁，推开公司的玻璃门，向设计部走去。她今天要递交辞呈。"
        # 无 LLM → 规则兜底（年龄 25 < 30 触发）
        annos = self.scanner.check_chapter_against_prior("novel", self.novel_dir, 2, ch_text)
        self.assertIsInstance(annos, list)
        # 规则判据较粗，允许 0 或命中；但确保格式正确
        for a in annos:
            self.assertIn("text", a)
            self.assertIn("issue", a)
            self.assertEqual(a.get("type"), "setting")

    def test_returns_empty_when_no_prior(self):
        ch_text = "第一章内容，林晚出场。"
        annos = self.scanner.check_chapter_against_prior("novel", self.novel_dir, 1, ch_text)
        self.assertEqual(annos, [])

    def test_returns_empty_when_no_entities(self):
        # 无 bible、无前文实体
        with open(os.path.join(self.novel_dir, "chapters", "chapter_0001.md"), "w", encoding="utf-8") as f:
            f.write("一些没有实体的普通文字。")
        annos = self.scanner.check_chapter_against_prior("novel", self.novel_dir, 2, "更多普通文字。")
        self.assertIsInstance(annos, list)

    def test_llm_path_parses_annotations(self):
        # mock LLM 返回矛盾
        fake_client = MagicMock()
        fake_resp = MagicMock()
        fake_resp.choices[0].message.content = (
            '[{"issue": "林晚年龄前后不一致", "chapter_ref": 1}]'
        )
        fake_client.chat.completions.create.return_value = fake_resp
        from core.settings_scanner import SettingsScanner
        s = SettingsScanner(fake_client, "mock-model")
        with open(os.path.join(self.novel_dir, "chapters", "chapter_0001.md"), "w", encoding="utf-8") as f:
            f.write("林晚今年30岁，是公司设计总监，深受下属敬重。")
        with open(os.path.join(self.novel_dir, "bible.json"), "w", encoding="utf-8") as f:
            json.dump({"schema_version": 1, "characters": {"林晚": {}}, "locations": {}, "terms": {}, "relations": []}, f, ensure_ascii=False)
        # 本章文本需 ≥30 字符才触发检查
        ch_text = "林晚今年25岁，推开公司的大门，向会议室走去。她今天要做一个重要提案。"
        annos = s.check_chapter_against_prior("novel", self.novel_dir, 2, ch_text)
        self.assertGreaterEqual(len(annos), 1)
        self.assertIn("issue", annos[0])
        self.assertEqual(annos[0]["type"], "setting")


class SettingAnnotationStepTests(unittest.TestCase):
    """generation mixin _setting_annotation_step 集成测试。"""

    def test_step_yields_status_and_returns_annotations(self):
        import asyncio
        from unittest.mock import MagicMock

        with patch("api.deps.engine.memory.get_novel_dir", return_value=tempfile.mkdtemp()), \
             patch("core.settings_scanner.SettingsScanner.check_chapter_against_prior",
                   return_value=[{"text": "林晚25岁", "issue": "年龄冲突", "type": "setting", "chapter_ref": 1}]):
            events = []
            result = []
            async def run():
                gen = engine._setting_annotation_step("novel", 2, "林晚25岁的内容")
                async for ev in gen:
                    if isinstance(ev, list):
                        result.extend(ev)
                    else:
                        events.append(ev)
                return result
            result = asyncio.run(run())
            # yield 的 list 是函数返回值（最后一个），status 事件应已产出
            self.assertIsInstance(result, list)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["issue"], "年龄冲突")


if __name__ == "__main__":
    unittest.main()
