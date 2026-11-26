import json
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core.scene_director_card import build_director_card, format_director_card
from core.surgical_humanizer import (
    build_rolling_cliche_block,
    extract_top_cliches,
    polish_flagged_paragraphs,
    record_rolling_cliches,
    score_paragraphs,
)


class _FakeMemory:
    def __init__(self):
        self.gs = {}

    def read(self, kind, novel_id):
        return self.gs

    def update(self, kind, novel_id, fn):
        self.gs = fn(self.gs)


class _FakeResilient:
    def __init__(self, reply_text: str):
        self.reply_text = reply_text
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.reply_text))]
        )


class TestHumanVoiceEngine(unittest.TestCase):
    def test_director_card_formats_valid_json(self):
        fake = _FakeResilient(
            json.dumps(
                {
                    "scenes": [
                        {
                            "sensory_anchor": "茶盏边缘裂纹发烫",
                            "subtext": "嘴上劝退，实则逼对方表态",
                            "lived_in_detail": "倒茶时溅出两滴水渍",
                        }
                    ]
                },
                ensure_ascii=False,
            )
        )
        block = build_director_card(
            fake,
            "deepseek-chat",
            {"title": "试探", "summary": "两人在茶馆博弈"},
        )
        self.assertIn("茶盏边缘裂纹发烫", block)
        self.assertIn("嘴上劝退，实则逼对方表态", block)
        self.assertIn("倒茶时溅出两滴水渍", block)

    def test_surgical_humanizer_skips_clean_prose_with_zero_llm_calls(self):
        clean_text = (
            "雨点砸在青石板上，溅起一阵冷雾。林川把湿透的袖口挽高半寸，推开了茶馆木门。\n\n"
            "“这么晚还来？”掌柜头也没抬，手里拨着算盘珠子，咔哒咔哒响个不停。"
        )
        fake = _FakeResilient("{}")
        out, meta = polish_flagged_paragraphs(fake, "deepseek-chat", clean_text)
        self.assertEqual(out, clean_text)
        self.assertEqual(fake.calls, 0)
        self.assertTrue(meta["skipped"])

    def test_surgical_humanizer_polishes_only_flagged_paragraphs(self):
        draft = (
            "雨点砸在青石板上，溅起一阵冷雾。林川把湿透的袖口挽高半寸，推开了茶馆木门。\n\n"
            "他感到心中充满愤怒不已的情绪，嘴角勾起一抹冷笑，眼中闪过一丝寒芒，空气仿佛凝固了一般。\n\n"
            "“坐吧。”掌柜推过来一只粗瓷碗，热气顺着指缝往上钻。"
        )
        scored = score_paragraphs(draft)
        self.assertEqual(scored[0]["index"], 1)

        fake = _FakeResilient(
            json.dumps(
                {
                    "1": "林川指节在桌沿叩了两下，没笑，只把半凉的茶碗往对方手边推了半寸，茶水晃出碗口。"
                },
                ensure_ascii=False,
            )
        )
        out, meta = polish_flagged_paragraphs(fake, "deepseek-chat", draft)
        self.assertEqual(fake.calls, 1)
        self.assertEqual(meta["polished"], 1)
        self.assertEqual(meta["indices"], [1])
        self.assertIn("指节在桌沿叩了两下", out)
        self.assertIn("雨点砸在青石板上", out)
        self.assertIn("热气顺着指缝往上钻", out)

    def test_rolling_cliches_persist_and_format_for_next_chapter(self):
        mem = _FakeMemory()
        ch1 = "他眼中闪过一丝惊讶，嘴角勾起一抹弧度，深吸一口气后缓缓开口。"
        found = record_rolling_cliches(mem, "book1", 1, ch1)
        self.assertIn("眼中闪过一丝", found)
        block = build_rolling_cliche_block(mem, "book1", 2)
        self.assertIn("前两章已高频出现以下套话", block)
        self.assertIn("眼中闪过一丝", block)

    def test_generation_and_writer_wired_for_zero_intervention_human_voice(self):
        gen_src = (ROOT / "backend/core/mixins/generation.py").read_text(encoding="utf-8")
        self.assertIn("build_director_card", gen_src)
        self.assertIn("polish_flagged_paragraphs", gen_src)
        self.assertIn("build_rolling_cliche_block", gen_src)
        pp_src = (ROOT / "backend/core/mixins/post_pipeline.py").read_text(encoding="utf-8")
        self.assertIn("record_rolling_cliches", pp_src)
        w_src = (ROOT / "backend/core/writer.py").read_text(encoding="utf-8")
        self.assertIn("frequency_penalty", w_src)


if __name__ == "__main__":
    unittest.main()
