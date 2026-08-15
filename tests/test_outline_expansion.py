"""章节大纲细化（旧大纲一句话 summary → scene_beats 事件契约）回归测试。"""

import importlib.util
import importlib.machinery
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


class ChapterNeedsExpansionTests(unittest.TestCase):
    def setUp(self):
        self.gen = engine  # engine 混入 GenerationMixin

    def test_old_outline_without_beats_needs_expansion(self):
        ch = {"number": 1, "title": "第一章", "summary": "主角觉醒金手指，打脸仇家。", "hook": "钩子"}
        self.assertTrue(engine._chapter_needs_outline_expansion(ch))

    def test_placeholder_beats_needs_expansion(self):
        ch = {
            "number": 2,
            "summary": "主角前往宗门拜师，遇到神秘老者。",
            "scene_beats": [
                {"beat": 1, "name": "开篇钩子", "key_action": "继续前进"},
                {"beat": 2, "name": "冲突升级", "key_action": "两人交谈"},
                {"beat": 3, "name": "转折高潮", "key_action": "主角前进"},
            ],
        }
        self.assertTrue(engine._chapter_needs_outline_expansion(ch))

    def test_new_outline_with_concrete_beats_no_expansion(self):
        ch = {
            "number": 3,
            "summary": "林晚在雨夜发现订婚消息，冲入陈默办公室对峙并辞职。",
            "scene_beats": [
                {"beat": 1, "name": "开篇钩子", "key_action": "林晚深夜加班，手机弹出陈默订婚新闻弹窗"},
                {"beat": 2, "name": "冲突升级", "key_action": "陈默电话承认订婚，林晚摔碎手机"},
                {"beat": 3, "name": "转折高潮", "key_action": "林晚冲入办公室当众撕毁方案书"},
                {"beat": 4, "name": "余波收束", "key_action": "林晚在雨里递交辞呈给主管"},
                {"beat": 5, "name": "章末钩子", "key_action": "陌生号码来电称有陈默黑料"},
            ],
        }
        self.assertFalse(engine._chapter_needs_outline_expansion(ch))

    def test_short_summary_needs_expansion(self):
        ch = {"number": 4, "title": "第四章", "summary": "继续推进", "hook": "悬念"}
        self.assertTrue(engine._chapter_needs_outline_expansion(ch))


class RenderSceneBeatsTests(unittest.TestCase):
    def test_renders_dict_beats(self):
        ch = {"scene_beats": [
            {"beat": 1, "name": "开篇", "key_action": "主角推门遇险"},
            {"beat": 2, "name": "高潮", "key_action": "主角反杀"},
        ]}
        text = engine._render_scene_beats(ch)
        self.assertIn("主角推门遇险", text)
        self.assertIn("开篇", text)

    def test_renders_string_beats(self):
        ch = {"scene_beats": ["主角遇袭", "反杀成功"]}
        text = engine._render_scene_beats(ch)
        self.assertIn("主角遇袭", text)
        self.assertIn("反杀成功", text)

    def test_empty_beats_returns_blank(self):
        self.assertEqual(engine._render_scene_beats({}), "")

    def test_hard_outline_includes_beats(self):
        ch = {
            "title": "第五章", "summary": "决战", "hook": "悬念",
            "characters": ["主角", "反派"],
            "scene_beats": [{"beat": 1, "name": "开战", "key_action": "双方拔剑对峙"}],
        }
        text = engine._hard_outline_text(ch)
        self.assertIn("核心事件：决战", text)
        self.assertIn("双方拔剑对峙", text)
        self.assertIn("角色：主角, 反派", text)


class ExpandChapterOutlineTests(unittest.TestCase):
    def test_expand_parses_beats_and_persists(self):
        # 用真实 engine 但 mock writer._create 返回固定 JSON
        fake_resp = MagicMock()
        fake_resp.choices[0].message.content = (
            '[{"beat":1,"name":"开篇","function":"引入","key_action":"秦昭睁眼遭遇圣杯追杀"},'
            '{"beat":2,"name":"升级","function":"加码","key_action":"秦昭用交易法则换情报"},'
            '{"beat":3,"name":"高潮","function":"爆发","key_action":"圣杯印记黑化反噬"},'
            '{"beat":4,"name":"收束","function":"沉淀","key_action":"秦昭锁定教会遗址"},'
            '{"beat":5,"name":"钩子","function":"悬念","key_action":"神秘黑手现身"}]'
        )
        ch = {"number": 1, "title": "第一章", "summary": "秦昭被圣杯追杀，用交易法则换情报。", "hook": "圣杯印记为何是黑色"}
        plan = {"title": "测试小说", "genre": "玄幻", "style": "热血", "outline": {"volumes": [{"chapters": [ch]}]}}
        tmp = tempfile.mkdtemp()

        with patch.object(engine.writer, "_create", return_value=fake_resp) as mock_create, \
             patch.object(engine.memory, "get_novel_dir", return_value=tmp):
            ok = engine._expand_chapter_outline(plan, ch, "test_novel", 1)
        self.assertTrue(ok)
        self.assertEqual(len(ch["scene_beats"]), 5)
        self.assertIn("秦昭睁眼遭遇圣杯追杀", ch["scene_beats"][0]["key_action"])
        # 持久化到 plan.json
        import json as _json
        saved = _json.load(open(os.path.join(tmp, "plan.json"), encoding="utf-8"))
        self.assertEqual(saved["outline"]["volumes"][0]["chapters"][0]["scene_beats"][0]["key_action"],
                         "秦昭睁眼遭遇圣杯追杀")

    def test_expand_failure_is_silent(self):
        ch = {"number": 1, "summary": "短", "hook": "h"}
        plan = {"title": "x", "outline": {"volumes": []}}
        with patch.object(engine.writer, "_create", side_effect=Exception("LLM down")):
            ok = engine._expand_chapter_outline(plan, ch, "n", 1)
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
