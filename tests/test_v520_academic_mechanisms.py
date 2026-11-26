import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core.scene_director_card import format_director_card, select_verbalized_candidate
from core.unified_post_processor import (
    UnifiedPostProcessor,
    build_active_beliefs_block,
    build_due_obligations_block,
    compute_ced_per_10k,
)


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


class TestV520AcademicMechanisms(unittest.TestCase):
    def test_verbalized_sampling_picks_lowest_typicality_valid_candidate(self):
        payload = {
            "candidates": [
                {
                    "p": 0.65,
                    "angle": "俗套拍桌对峙",
                    "scenes": [{"tactical_goal": "大吵一架", "sensory_nouns": ["桌子"]}],
                },
                {
                    "p": 0.15,
                    "angle": "借递茶叶罐暗传焦边账本",
                    "scenes": [
                        {
                            "tactical_goal": "表面劝退实则递证",
                            "info_asymmetry": "周伯知厂长锁门，林川不知",
                            "power_shift": "僵持→暗中结盟",
                            "sensory_nouns": ["铁皮罐", "茶渍", "旧算盘"],
                        }
                    ],
                },
                {
                    "p": 0.05,
                    "angle": "空场景无效候选",
                    "scenes": [],
                },
            ]
        }
        chosen = select_verbalized_candidate(payload)
        self.assertEqual(chosen["angle"], "借递茶叶罐暗传焦边账本")
        rendered = format_director_card(payload)
        self.assertIn("反套路切入视角：借递茶叶罐暗传焦边账本", rendered)
        self.assertIn("铁皮罐 / 茶渍 / 旧算盘", rendered)

    def test_tom_beliefs_future_obligations_and_constory_ced_persist_and_filter(self):
        payload = {
            "bridge": {"summary": "林川拿到焦边账本", "ending_scene": "巷口黑车打火", "next_beat": "甩开跟踪", "unresolved_tension": "黑车"},
            "summary_entry": {"summary": "茶铺暗中递证", "key_events": ["周伯递出焦边账本"], "characters": ["林川", "周伯"]},
            "character_updates": {"周伯": {"location": "茶铺", "status": "警觉"}},
            "foreshadow_updates": [],
            "consistency_violations": [
                {
                    "severity": "P1",
                    "dimension": "timeline_plot",
                    "quote": "雨已经停了",
                    "description": "前段仍在下雨未交代雨停",
                    "fix_hint": "补一句雨势渐歇",
                }
            ],
            "character_quotes": {"周伯": ["这雨下得，骨头缝都发潮。"]},
            "character_beliefs": {
                "林川": {"knows": ["周伯偷偷给了焦边账本"], "misbelieves": ["以为周伯收了封口费"]},
                "陈明远": {"knows": ["当年锁了仓库门"], "misbelieves": ["以为旧账本已烧毁"]},
            },
            "future_obligations": [
                {"title": "核实焦边账本上的夜班入库记录", "due_by_chapter": 5, "owner": "林川", "status": "open"},
                {"title": "远期大结局伏笔", "due_by_chapter": 30, "owner": "陈明远", "status": "open"},
            ],
        }
        mem = _Mem()
        text = "这是一段两千字的正文内容。" * 200
        upp = UnifiedPostProcessor(_FakeClient(json.dumps(payload, ensure_ascii=False)), "deepseek-chat")
        res = upp.process_and_persist(mem, "book1", 3, text, {"title": "旧账本"})

        # Verify ConStory-Bench CED calculation
        self.assertAlmostEqual(res["ced_per_10k"], compute_ced_per_10k(1, len(text)), places=2)
        self.assertIn("3", mem.gs.get("ced_history", {}))

        # Verify active-cast ToM belief filtering (excludes non-present 陈明远)
        beliefs_block = build_active_beliefs_block(mem, "book1", active_characters=["林川", "周伯"])
        self.assertIn("林川", beliefs_block)
        self.assertIn("以为周伯收了封口费", beliefs_block)
        self.assertNotIn("陈明远", beliefs_block)

        # Verify due-window future obligations filtering (Ch4 lookahead=3 includes due<=7, excludes due=30)
        ob_block = build_due_obligations_block(mem, "book1", chapter_num=4, lookahead=3)
        self.assertIn("核实焦边账本上的夜班入库记录", ob_block)
        self.assertNotIn("远期大结局伏笔", ob_block)


if __name__ == "__main__":
    unittest.main()
