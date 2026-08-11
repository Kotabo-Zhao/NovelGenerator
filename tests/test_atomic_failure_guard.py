"""原子化章节失败状态回归测试。"""
import asyncio
import os
import sys
import types
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

try:
    import openai  # noqa: F401
except ModuleNotFoundError:
    fake_openai = types.ModuleType("openai")
    fake_openai.OpenAI = object
    sys.modules["openai"] = fake_openai

from core.atomic_writer import AtomicWriter


class AtomicFailureGuardTests(unittest.TestCase):
    def test_failed_beat_aborts_stream_before_assembly(self):
        writer = AtomicWriter(None, "mock")
        writer.write_beat = lambda *args, **kwargs: {
            "text": "",
            "failed": True,
            "candidates": [],
            "selected_index": -1,
            "temperature": 0.8,
        }
        beat = SimpleNamespace(index=0, function="opening_hook", temperature=0.8)

        async def consume():
            events = []
            async for event in writer.write_beats_stream([beat]):
                events.append(event)
            return events

        with self.assertRaises(RuntimeError):
            asyncio.run(consume())


if __name__ == "__main__":
    unittest.main()
