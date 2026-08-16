"""v2.0.1 修复：长文防重复事件清单 + 大纲修改保留基线 + 「⋯」菜单定位。"""

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


class EventTimelineTests(unittest.TestCase):
    """问题2：已发生事件清单（全书防重复）。"""

    def test_writer_system_has_anti_repeat_rule(self):
        from core.writer import WRITER_SYSTEM
        self.assertIn("防重复", WRITER_SYSTEM)
        self.assertIn("已发生事件清单", WRITER_SYSTEM)
        self.assertIn("严禁复述", WRITER_SYSTEM)

    def test_context_includes_event_timeline(self):
        """build_writer_context 应产出「已发生事件清单」（真实书验证，不 mock 引擎）。"""
        from core.shared_memory import SharedMemoryManager
        from api.deps import engine

        # 用真实 novels 目录中的书（测试环境有 NOVELS_DIR 且有已写章节的书）
        import glob
        novels_dir = os.environ.get("NOVELS_DIR", os.path.join(os.path.dirname(__file__), "..", "novels"))
        candidates = []
        for d in os.listdir(novels_dir) if os.path.isdir(novels_dir) else []:
            cd = os.path.join(novels_dir, d, "chapters")
            if os.path.isdir(cd):
                n = len([f for f in os.listdir(cd) if f.endswith(".md")])
                if n >= 3:
                    candidates.append((d, n))
        if not candidates:
            self.skipTest("无已写≥3章的小说，跳过真实上下文验证")
        novel_id = candidates[0][0]
        plan = engine.get_novel(novel_id)
        ch_outline = None
        for v in plan.get("outline", {}).get("volumes", []):
            for ch in v.get("chapters", []):
                if ch.get("number") == 6:
                    ch_outline = ch
                    break
            if ch_outline:
                break
        if not ch_outline:
            ch_outline = {"number": 6, "title": "第六章", "summary": "继续", "hook": "h"}
        ctx = engine.memory.build_writer_context(novel_id, 6, ch_outline)
        self.assertIn("已发生事件清单", ctx)
        self.assertIn("严禁重演", ctx)


class OutlineRegenBaselineTests(unittest.TestCase):
    """问题3：大纲修改必须带当前大纲作为基线（完整覆盖长篇）。"""

    def test_global_regen_includes_full_compressed_outline(self):
        import asyncio
        from core.outline_interactive import OutlineInteractive
        from unittest.mock import MagicMock

        oi = OutlineInteractive(client=MagicMock(), model="mock")
        # 长篇 30 章（模拟）
        chapters = [{"number": i, "title": f"第{i}章", "summary": f"事件{i}", "hook": "h"} for i in range(1, 31)]
        plan = {
            "genre": "玄幻", "style": "热血", "target_words": 100000,
            "worldbuilding": {"era": "古代"}, "characters": {"protagonist": {"name": "林晚"}},
            "outline": {"volumes": [{"number": 1, "title": "第一卷", "chapters": chapters}],
                        "total_chapters": 30},
        }

        captured = {}
        async def fake_call(prompt, planner, name, max_tokens):
            captured["prompt"] = prompt
            captured["max_tokens"] = max_tokens
            return {"volumes": [{"number": 1, "title": "第一卷", "chapters": chapters}], "total_chapters": 30}

        oi._safe_llm_call = fake_call
        result = asyncio.run(oi._regenerate_with_prompt(plan, "加一条感情线", MagicMock(), mode="global"))
        # 压缩基线必须包含全部 30 章（不是 6000 截断丢后半）
        for i in (1, 15, 30):
            self.assertIn(f"Ch{i}", captured["prompt"], f"压缩基线缺少 Ch{i}（长篇后半丢失）")
        self.assertIn("事件30", captured["prompt"])
        self.assertGreaterEqual(captured["max_tokens"], 20000)  # 输出容量足够

    def test_decomposer_sees_all_chapters(self):
        """feedback_decomposer 的 outline context 必须含全部章节（原只前10章）。"""
        from core.feedback_decomposer import FeedbackDecomposer
        fd = FeedbackDecomposer()
        chapters = [{"number": i, "title": f"第{i}章", "summary": f"事件{i}"} for i in range(1, 21)]
        plan = {"outline": {"total_chapters": 20, "volumes": [{"number": 1, "title": "V1", "chapters": chapters}]}}
        ctx = fd._build_outline_context(plan)
        self.assertIn("Ch20", ctx)   # 原 [:10] 会丢 Ch11-20
        self.assertIn("事件20", ctx)


class WriteMoreMenuTests(unittest.TestCase):
    """问题1：write-more-menu 定位锚点。"""

    def test_wb_tabs_has_position_relative(self):
        idx = open(os.path.join(os.path.dirname(__file__), "..", "web", "index.html"), encoding="utf-8").read()
        self.assertIn("position:relative", idx)  # .wb-tabs
        self.assertIn("write-more-menu", idx)
        self.assertIn("z-index:300", idx)


if __name__ == "__main__":
    unittest.main()
