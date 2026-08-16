"""v2.7.6 字数放开：目标字数自由填写（下限100），planner 章节规划适配小字数，
writer/quality_gate 截断下限放宽，atomic beat 字数小目标缩放。"""

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


class ApiTargetWordsTests(unittest.TestCase):
    """API 校验：下限 1000 → 100，超短篇可创建。"""

    def test_min_100_accepted(self):
        from pydantic import ValidationError
        from api.routers.novels import CreateNovelRequest
        try:
            req = CreateNovelRequest(target_words=100)
            self.assertEqual(req.target_words, 100)
        except ValidationError:
            self.fail("target_words=100 应被接受（下限已放开到100）")

    def test_below_100_rejected(self):
        from pydantic import ValidationError
        from api.routers.novels import CreateNovelRequest
        with self.assertRaises(ValidationError):
            CreateNovelRequest(target_words=99)

    def test_large_still_accepted(self):
        from api.routers.novels import CreateNovelRequest
        req = CreateNovelRequest(target_words=2000000)
        self.assertEqual(req.target_words, 2000000)

    def test_over_cap_rejected(self):
        from pydantic import ValidationError
        from api.routers.novels import CreateNovelRequest
        with self.assertRaises(ValidationError):
            CreateNovelRequest(target_words=2000001)


class ComputeChapterPlanTests(unittest.TestCase):
    """planner 章节规划：小字数自然少章，长篇行为不变。"""

    def test_flash_800_single_chapter(self):
        from core.planner import compute_chapter_plan
        p = compute_chapter_plan(800)
        self.assertEqual(p["total_chapters"], 1)
        self.assertEqual(p["chapter_words"], 800)  # 每章字数=总字数
        self.assertEqual(p["vol_count"], 1)

    def test_5000_two_chapters(self):
        from core.planner import compute_chapter_plan
        p = compute_chapter_plan(5000)
        self.assertEqual(p["total_chapters"], 2)
        self.assertEqual(p["chapter_words"], 2500)
        self.assertEqual(p["vol_count"], 1)

    def test_100_lower_bound(self):
        from core.planner import compute_chapter_plan
        p = compute_chapter_plan(100)
        self.assertEqual(p["total_chapters"], 1)
        self.assertEqual(p["chapter_words"], 100)

    def test_large_unchanged(self):
        """长篇：100000 字 → 50 章 × 2000 字（与旧逻辑 max(8, tw//2000) 一致）"""
        from core.planner import compute_chapter_plan
        p = compute_chapter_plan(100000)
        self.assertEqual(p["total_chapters"], 50)
        self.assertEqual(p["chapter_words"], 2000)

    def test_16000_unchanged(self):
        """16000 字 → 8 章 × 2000（旧逻辑下限 8 章的临界点行为一致）"""
        from core.planner import compute_chapter_plan
        p = compute_chapter_plan(16000)
        self.assertEqual(p["total_chapters"], 8)
        self.assertEqual(p["chapter_words"], 2000)


class CheckTruncationTests(unittest.TestCase):
    """writer._check_truncation：小目标不再被 500 字硬底卡死。"""

    def test_small_target_200_of_300_ok(self):
        from core.writer import _check_truncation
        truncated, reason = _check_truncation("。".join(["好" * 30] * 7), 300)
        # 210 字 ≥ max(40, 150) → 不截断（若结尾完整）
        if truncated:
            self.assertNotIn("长度不足", reason)

    def test_large_target_still_enforces(self):
        """2000 字目标 600 字 → 仍判截断（0.5×2000=1000 下限不变）"""
        from core.writer import _check_truncation
        truncated, reason = _check_truncation("今天天气不错。" * 100, 2000)
        self.assertTrue(truncated)
        self.assertIn("长度不足", reason)

    def test_flash_800_400_ok(self):
        from core.writer import _check_truncation
        # 400 字 ≥ max(40, 400) → 达标不截断
        text = "。" .join(["第一章事件描写" * 8] * 5)
        if len(text) < 400:
            text = text + "。".join(["补充内容" * 6] * 10)
        truncated, reason = _check_truncation(text, 800)
        if truncated:
            self.assertNotIn("长度不足", reason)


class QualityGateTests(unittest.TestCase):
    """quality_gate：小目标最低字数下限 120 → 50。"""

    def test_minimum_100_target(self):
        from core.quality_gate import validate_chapter_text
        r = validate_chapter_text("这是一个完整的短故事。" * 10, target_words=100)
        self.assertEqual(r["minimum_chars"], 50)

    def test_minimum_2000_target_unchanged(self):
        from core.quality_gate import validate_chapter_text
        r = validate_chapter_text("这是一个完整的短故事。" * 50, target_words=2000)
        self.assertEqual(r["minimum_chars"], 500)

    def test_flash_300_short_text_passes(self):
        """300 字目标、100 字正文 → 通过（minimum=max(50, 75)=75）"""
        from core.quality_gate import validate_chapter_text
        r = validate_chapter_text("第一句。第二句。第三句！" * 10, target_words=300)
        self.assertGreaterEqual(r["chars"], r["minimum_chars"])


class BeatScaleTests(unittest.TestCase):
    """beat 字数小目标缩放：超短篇不被模板撑爆，长篇模板不变。"""

    def _decompose(self, target_words):
        from core.beat_decomposer import BeatDecomposer
        outline = {
            "number": 1, "title": "第1章", "summary": "测试",
            "emotion_curve": "平稳→紧张", "characters": ["主角"],
            "hook": "悬念", "target_words": target_words,
        }
        beats = BeatDecomposer(seed=42).decompose(outline, 1)
        return beats

    def test_flash_800_scaled(self):
        beats = self._decompose(800)
        total_max = sum(b.max_words for b in beats)
        self.assertLessEqual(total_max, 1100)  # 缩放后总量接近 800，不被撑到 2500+

    def test_normal_2000_unchanged(self):
        beats = self._decompose(2000)
        # 2000 ≥ 1500 阈值 → 不缩放，保持模板（约 1700 字总量，明显大于 800 缩放后）
        self.assertGreater(sum(b.max_words for b in beats), 1400)


class ClampOutlineTests(unittest.TestCase):
    """planner._clamp_outline_to_plan：章节数强制=规划数，概要并入保留章。"""

    def _outline(self, n_chapters, target_words=2000):
        vols = [{"number": 1, "title": "第一卷", "act": "第一幕·建置",
                 "theme": "", "act_function": "", "chapters": [
                     {"number": i + 1, "title": f"第{i+1}章",
                      "summary": f"第{i+1}章概要内容", "hook": f"钩子{i+1}",
                      "target_words": target_words}
                     for i in range(n_chapters)
                 ]}]
        return {"volumes": vols, "total_chapters": n_chapters}

    def test_3_chapters_clamped_to_1(self):
        """800字 → 3章被压成1章800字，被弃概要并入保留章"""
        from core.planner import _clamp_outline_to_plan
        outline = self._outline(3)
        out = _clamp_outline_to_plan(outline, 1, 800)
        self.assertEqual(out["total_chapters"], 1)
        ch = out["volumes"][0]["chapters"][0]
        self.assertEqual(ch["target_words"], 800)
        self.assertIn("第1章概要内容", ch["summary"])
        self.assertIn("第2章概要内容", ch["summary"])  # 被弃章概要并入
        self.assertIn("第3章概要内容", ch["summary"])

    def test_2_chapters_padded_to_3(self):
        """3000字 → 2章补到3章，每章1000字"""
        from core.planner import _clamp_outline_to_plan
        outline = self._outline(2)
        out = _clamp_outline_to_plan(outline, 3, 3000)
        self.assertEqual(out["total_chapters"], 3)
        words = [c["target_words"] for c in out["volumes"][0]["chapters"]]
        self.assertEqual(words, [1000, 1000, 1000])

    def test_unchanged_when_matches(self):
        """50000字 → 25章恰好=规划25章，不变"""
        from core.planner import _clamp_outline_to_plan
        outline = self._outline(25)
        out = _clamp_outline_to_plan(outline, 25, 50000)
        self.assertEqual(out["total_chapters"], 25)
        self.assertEqual(out["volumes"][0]["chapters"][0]["target_words"], 2000)

    def test_empty_outline_filled(self):
        """空大纲 → 补满规划章数"""
        from core.planner import _clamp_outline_to_plan
        outline = {"volumes": [], "total_chapters": 0}
        out = _clamp_outline_to_plan(outline, 2, 2000)
        self.assertEqual(out["total_chapters"], 2)
        self.assertEqual([c["target_words"] for c in out["volumes"][0]["chapters"]],
                         [1000, 1000])


class TrimToTargetTests(unittest.TestCase):
    """writer._trim_to_target：超短篇超长裁剪，长篇不动。"""

    def test_flash_long_trimmed(self):
        from core.writer import _trim_to_target
        text = "第一句。" + "内容填充。" * 400  # 1600+ 字
        out = _trim_to_target(text, 800)
        self.assertLessEqual(len(out), 800 + 1)
        self.assertTrue(out.endswith("。"))  # 保留完整句子

    def test_flash_short_untouched(self):
        from core.writer import _trim_to_target
        text = "这是一个简短的故事。" * 50  # 500 字 < 800*1.35
        out = _trim_to_target(text, 800)
        self.assertEqual(out, text)

    def test_long_never_trimmed(self):
        from core.writer import _trim_to_target
        text = "内容。" * 3000  # 6000 字
        out = _trim_to_target(text, 2000)
        self.assertEqual(out, text)  # 长篇不裁剪


if __name__ == "__main__":
    unittest.main()
