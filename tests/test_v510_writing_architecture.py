import json
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core.scene_director_card import build_director_card
from core.unified_post_processor import UnifiedPostProcessor, build_active_voice_bank_block
from core.writer import WRITER_SYSTEM


class _Mem:
    def __init__(self):
        self.gs = {}

    def read(self, kind, novel_id):
        return self.gs

    def update(self, kind, novel_id, fn):
        self.gs = fn(self.gs)

    def save_bridge(self, novel_id, ch, bridge):
        pass


class _FakeClient:
    def __init__(self, content: str):
        self.content = content

    def create(self, **kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))]
        )


class TestV510WritingArchitecture(unittest.TestCase):
    def test_writer_system_is_slim_positive_craft_and_keeps_required_anchors(self):
        self.assertLess(len(WRITER_SYSTEM), 1400, f"WRITER_SYSTEM should be slimmed <1400 chars (got {len(WRITER_SYSTEM)})")
        negs = len(re.findall(r"(?:禁止|严禁|不要|绝不|切忌|不得|不能|避免)", WRITER_SYSTEM))
        self.assertLessEqual(negs, 10, f"Too many negative imperatives in WRITER_SYSTEM: {negs}")
        for required in ("{style_guide}", "{target_words}", "防重复", "已发生事件清单", "严禁复述"):
            self.assertIn(required, WRITER_SYSTEM)

    def test_dramatic_vector_director_card_formats_new_schema(self):
        fake = _FakeClient(
            json.dumps(
                {
                    "scenes": [
                        {
                            "tactical_goal": "林川套问火因，周伯想速打发走人",
                            "info_asymmetry": "周伯知厂长锁门，林川以为他收了封口费",
                            "power_shift": "僵持试探→暗中递证",
                            "sensory_nouns": ["铁皮檐", "茶渍", "旧算盘"],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        )
        out = build_director_card(fake, "deepseek-chat", {"title": "旧账本", "summary": "茶铺试探"})
        self.assertIn("博弈目标：", out)
        self.assertIn("信息差与潜台词：", out)
        self.assertIn("局势翻转：", out)
        self.assertIn("铁皮檐 / 茶渍 / 旧算盘", out)

    def test_unified_post_processor_extracts_and_filters_active_voice_bank(self):
        payload = {
            "bridge": {"summary": "林川拿到焦边账本", "ending_scene": "巷口黑车打火", "next_beat": "甩开跟踪", "unresolved_tension": "黑车是谁"},
            "summary_entry": {"summary": "茶铺试探", "key_events": ["周伯递出焦边账本"], "characters": ["林川", "周伯"]},
            "character_updates": {"周伯": {"location": "茶铺", "status": "紧张"}},
            "foreshadow_updates": [],
            "consistency_violations": [],
            "character_quotes": {
                "周伯": ["这雨下得，骨头缝都发潮，喝口热的再走。"],
                "陈明远": ["有些账，烧了比留着体面。"],
            },
        }
        mem = _Mem()
        upp = UnifiedPostProcessor(_FakeClient(json.dumps(payload, ensure_ascii=False)), "deepseek-chat")
        upp.process_and_persist(mem, "book1", 3, "这是一段足够长的测试正文。" * 15, {"title": "旧账本"})

        # Only active characters ("周伯") should be injected, filtering out non-present "陈明远"
        block = build_active_voice_bank_block(mem, "book1", active_characters=["林川", "周伯"])
        self.assertIn("周伯", block)
        self.assertIn("骨头缝都发潮", block)
        self.assertNotIn("陈明远", block)


if __name__ == "__main__":
    unittest.main()
