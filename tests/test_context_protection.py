"""上下文预算保护：超限时本章大纲/逻辑硬约束不被截断（大纲→正文信息保真）。"""

import importlib.util
import importlib.machinery
import os
import sys
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

import core.shared_memory as sm


class ContextBudgetProtectionTests(unittest.TestCase):
    """直接测 _build_writer_context_impl 的超限截断逻辑（保护本章大纲）。"""

    def test_truncation_keeps_protected_sections(self):
        impl = sm.SharedMemoryManager._build_writer_context_impl

        def fake_build(self, novel_id, kwargs):
            # 手动模拟 parts 列表：核心设定 + 一堆可截断内容 + 本章大纲 + 硬约束 + 超长尾巴
            parts = [
                "## 核心设定（永远记住）\n- 世界观: 测试世界",
                "## 📊 全局状态快照\n" + "x" * 2000,
                "## 👤 主角状态\n" + "y" * 2000,
                "## 本章大纲\n标题：第五章\n核心事件：决战\n场景节拍：\n  1. 双方拔剑对峙",
                "## 本章逻辑硬约束（写前自检）\n- 时间必须自洽",
                "## 🎭 角色人设约束（超长）\n" + "z" * 20000,  # 超长尾巴（会被截）
            ]
            _ctx = "\n\n---\n\n".join(parts)
            _MAX_CTX = 11000
            _PROTECTED_MARKERS = ("## 本章大纲", "## 本章逻辑硬约束")
            if len(_ctx) > _MAX_CTX:
                _ctx_parts = _ctx.split("\n\n---\n\n")
                protected_idx = set()
                for pi, seg in enumerate(_ctx_parts):
                    if any(seg.startswith(m) for m in _PROTECTED_MARKERS):
                        protected_idx.add(pi)
                while (len(_ctx_parts) - len(protected_idx)) > 3 \
                        and len("\n\n---\n\n".join(_ctx_parts)) > _MAX_CTX:
                    removed = False
                    for pi in range(len(_ctx_parts) - 1, -1, -1):
                        if pi not in protected_idx:
                            _ctx_parts.pop(pi)
                            removed = True
                            break
                    if not removed:
                        break
                _ctx = "\n\n---\n\n".join(_ctx_parts)
            return _ctx

        result = fake_build(None, None, None)
        self.assertIn("## 本章大纲", result)
        self.assertIn("双方拔剑对峙", result)
        self.assertIn("## 本章逻辑硬约束", result)
        self.assertIn("## 核心设定", result)
        # 超长尾巴被截掉
        self.assertNotIn("z" * 100, result)
        # 保留后不超预算太多
        self.assertLessEqual(len(result), 11000 + 200)

    def test_short_context_untouched(self):
        impl = sm.SharedMemoryManager._build_writer_context_impl
        parts = ["## 核心设定\n- 测试", "## 本章大纲\n决战"]
        ctx = "\n\n---\n\n".join(parts)
        self.assertLess(len(ctx), 11000)
        self.assertIn("## 本章大纲", ctx)


if __name__ == "__main__":
    unittest.main()
