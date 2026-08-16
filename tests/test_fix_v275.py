"""v2.7.5 摘要完整性修复：
1. 原子化路径此前无摘要逻辑 → 新增懒补 _backfill_missing_summaries
2. L2c 事件清单 fallback 大纲计划时必须标注「未确认」，不得冒充已发生
"""

import asyncio
import importlib.util
import importlib.machinery
import json
import os
import sys
import tempfile
import types
import unittest

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


class FakeMemory:
    """最小 fake：模拟 SharedMemoryManager 的 read/scan/read_chapter/update"""

    def __init__(self, state, disk, chapters):
        self._state = state
        self._disk = list(disk)
        self._chapters = dict(chapters)
        self.update_calls = []

    def read(self, memory_type, novel_id, skip_cache=False):
        return self._state

    def scan_chapters(self, novel_id):
        return list(self._disk)

    def read_chapter(self, novel_id, ch):
        return self._chapters.get(ch)

    def update(self, memory_type, novel_id, fn):
        # 模拟原子读-改-写（浅拷贝隔离调用方）
        new_state = fn(dict(self._state))
        self._state = new_state
        self.update_calls.append(memory_type)
        return new_state


class FakeSummarizer:
    def __init__(self):
        self.batch_calls = []

    def summarize_batch(self, contents, novel_id=""):
        self.batch_calls.append(sorted(contents.keys()))
        return {
            ch: {"chapter": ch, "summary": f"摘要{ch}", "key_events": [f"事件{ch}"]}
            for ch in contents
        }


def make_engine(state, disk, chapters, summarizer=None):
    from core.mixins.generation import GenerationMixin
    eng = GenerationMixin.__new__(GenerationMixin)
    eng.memory = FakeMemory(state, disk, chapters)
    eng.chapter_summarizer = summarizer or FakeSummarizer()
    return eng


def run(coro):
    return asyncio.run(coro)


class BackfillTests(unittest.TestCase):
    """问题1：懒补 _backfill_missing_summaries 的窗口/排序/上限逻辑。"""

    def test_backfill_latest_first_limit5(self):
        """写第20章：窗口1-19 全缺 → 补最近5章（19,18,17,16,15），从最新往前。"""
        state = {"summaries": {}}
        disk = list(range(1, 20))
        chapters = {ch: f"正文第{ch}章内容" * 30 for ch in disk}  # >200 字符
        summ = FakeSummarizer()
        eng = make_engine(state, disk, chapters, summ)
        n = run(eng._backfill_missing_summaries("test", 20))
        self.assertEqual(n, 5)
        self.assertEqual(summ.batch_calls, [[15, 16, 17, 18, 19]])
        # 写回 summaries
        self.assertEqual(sorted(eng.memory._state["summaries"].keys()),
                         ["15", "16", "17", "18", "19"])

    def test_backfill_skips_existing(self):
        """已有摘要的章跳过：summaries 有 5,6,7 → missing = 1,2,3,4,8,9 → 补 9,8,4,3,2"""
        state = {"summaries": {"5": {"summary": "已有"}, "6": {"summary": "已有"},
                               "7": {"summary": "已有"}}}
        disk = list(range(1, 10))
        chapters = {ch: f"正文第{ch}章内容" * 30 for ch in disk}
        summ = FakeSummarizer()
        eng = make_engine(state, disk, chapters, summ)
        n = run(eng._backfill_missing_summaries("test", 10))
        self.assertEqual(n, 5)
        self.assertEqual(summ.batch_calls[0], [2, 3, 4, 8, 9])
        # 已有摘要未被覆盖
        self.assertEqual(eng.memory._state["summaries"]["5"]["summary"], "已有")

    def test_backfill_no_missing_returns_0(self):
        """窗口内全有摘要 → 不调 summarize，返回 0"""
        state = {"summaries": {str(i): {"summary": f"s{i}"} for i in range(1, 11)}}
        disk = list(range(1, 11))
        chapters = {ch: f"正文第{ch}章内容" * 30 for ch in disk}
        summ = FakeSummarizer()
        eng = make_engine(state, disk, chapters, summ)
        n = run(eng._backfill_missing_summaries("test", 11))
        self.assertEqual(n, 0)
        self.assertEqual(summ.batch_calls, [])

    def test_backfill_short_content_skipped(self):
        """正文 <200 字符的章不补（可能是空壳/损坏文件）"""
        state = {"summaries": {}}
        disk = [1, 2, 3]
        chapters = {1: "短", 2: f"长" * 500, 3: "短"}
        summ = FakeSummarizer()
        eng = make_engine(state, disk, chapters, summ)
        n = run(eng._backfill_missing_summaries("test", 4))
        self.assertEqual(n, 1)
        self.assertEqual(summ.batch_calls, [[2]])
        self.assertIn("2", eng.memory._state["summaries"])
        self.assertNotIn("1", eng.memory._state["summaries"])
        self.assertNotIn("3", eng.memory._state["summaries"])

    def test_backfill_chapter1_skipped(self):
        """第一章无前文窗口 → 返回 0"""
        eng = make_engine({"summaries": {}}, [], {})
        n = run(eng._backfill_missing_summaries("test", 1))
        self.assertEqual(n, 0)

    def test_backfill_failure_swallowed(self):
        """summarize_batch 抛异常 → 返回 0，不崩溃"""

        class BoomSummarizer:
            def summarize_batch(self, contents, novel_id=""):
                raise RuntimeError("LLM down")

        state = {"summaries": {}}
        disk = [1, 2, 3]
        chapters = {ch: f"正文第{ch}章内容" * 30 for ch in disk}
        eng = make_engine(state, disk, chapters, BoomSummarizer())
        n = run(eng._backfill_missing_summaries("test", 4))
        self.assertEqual(n, 0)


class L2cFallbackTests(unittest.TestCase):
    """问题2：L2c 事件清单缺摘要 fallback 大纲时必须标注「未确认」"""

    def _make_novel(self, tmp, total_ch=9, summaries=None):
        """构造最小书目录：plan.json（大纲带 summary）+ chapters + global_state"""
        novel_dir = os.path.join(tmp, "测试书")
        os.makedirs(os.path.join(novel_dir, "chapters"), exist_ok=True)
        plan = {
            "title": "测试书",
            "genre": "玄幻",
            "style": "热血",
            "characters": {"protagonist": {"name": "主角", "identity": "少年"}},
            "worldbuilding": {"era": "古代", "power_system": "灵力", "core_conflict": "复仇"},
            "outline": {
                "total_chapters": 25,
                "volumes": [{
                    "chapters": [
                        {"number": i, "title": f"第{i}章", "summary": f"大纲计划{i}", "hook": ""}
                        for i in range(1, total_ch + 1)
                    ]
                }],
            },
        }
        with open(os.path.join(novel_dir, "plan.json"), "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False)
        for i in range(1, total_ch + 1):
            with open(os.path.join(novel_dir, "chapters", f"chapter_{i:04d}.md"),
                      "w", encoding="utf-8") as f:
                f.write(f"# 第{i}章\n\n正文内容第{i}章" * 20)
        gs = {"summaries": summaries or {}}
        with open(os.path.join(novel_dir, "global_state.json"), "w", encoding="utf-8") as f:
            json.dump(gs, f, ensure_ascii=False)
        with open(os.path.join(novel_dir, "state.json"), "w", encoding="utf-8") as f:
            json.dump({"completed_chapters": list(range(1, total_ch + 1))}, f)
        return novel_dir

    def test_l2c_fallback_marked_plan(self):
        """9章已写、summaries 只有 7/8/9 → 写第10章时 1-6 章 fallback 大纲且标注未确认"""
        from core.shared_memory import SharedMemoryManager
        with tempfile.TemporaryDirectory() as tmp:
            novel_dir = self._make_novel(
                tmp,
                summaries={"7": {"summary": "真实摘要7"}, "8": {"summary": "真实摘要8"},
                           "9": {"summary": "真实摘要9"}},
            )
            smm = SharedMemoryManager(novels_dir=tmp)
            ctx = smm.build_writer_context("测试书", 10, {"number": 10, "title": "第10章"})
            # 真实摘要出现在清单里（近3章 7/8/9）
            self.assertIn("真实摘要7", ctx)
            self.assertIn("真实摘要8", ctx)
            # 缺摘要的早章 fallback 大纲计划，且明确标注未确认
            self.assertIn("[大纲计划·未确认]", ctx)
            self.assertIn("大纲计划1", ctx)
            # 大纲计划不能冒充「已发生」
            self.assertNotIn("第1章(已发生): 大纲计划1", ctx)

    def test_l2c_real_summaries_not_marked(self):
        """已有真实摘要的章不标「未确认」"""
        from core.shared_memory import SharedMemoryManager
        with tempfile.TemporaryDirectory() as tmp:
            novel_dir = self._make_novel(
                tmp,
                summaries={str(i): {"summary": f"真实摘要{i}"} for i in range(1, 10)},
            )
            smm = SharedMemoryManager(novels_dir=tmp)
            ctx = smm.build_writer_context("测试书", 10, {"number": 10, "title": "第10章"})
            self.assertIn("真实摘要1", ctx)
            self.assertIn("真实摘要9", ctx)
            self.assertNotIn("[大纲计划·未确认]", ctx)


if __name__ == "__main__":
    unittest.main()
