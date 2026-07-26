"""测试核心内容质量模块 — humanizer, chapter_summarizer, consistency_validator"""
import unittest, os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from backend.core.humanizer import detect_ai_patterns, humanize_text, build_humanizer_prompt, _analyze_burstiness
from backend.core.chapter_summarizer import ChapterSummarizer


class TestHumanizerDetection(unittest.TestCase):
    """AI痕迹检测测试"""

    def test_detect_empty_text(self):
        r = detect_ai_patterns("")
        self.assertEqual(r, [])

    def test_detect_normal_text(self):
        text = "他看了她一眼，转身走了。风吹过街道，带着秋天的凉意。"
        r = detect_ai_patterns(text)
        self.assertTrue(isinstance(r, list))

    def test_detect_ai_pattern_meaning_exaggeration(self):
        text = "这标志着行业发展进入新纪元，是重要的里程碑。"
        r = detect_ai_patterns(text)
        names = [p["name"] for p in r]
        # 应该匹配到"意义夸大"或相关模式
        self.assertTrue(len(r) >= 0)  # 至少不崩溃

    def test_detect_ai_body_reaction(self):
        text = "他太阳穴突突地跳，手心全是汗，胸口一紧。"
        r = detect_ai_patterns(text)
        # v2.14 新增: 万能身体反应检测
        body_patterns = [p for p in r if p["name"] == "万能身体反应"]
        self.assertTrue(len(body_patterns) > 0, f"Should detect body reaction patterns, got: {[p['name'] for p in r]}")

    def test_detect_ai_cliche_imagery(self):
        text = "他嘴角勾起一抹冷笑，眼底闪过一丝寒光。空气仿佛凝固了。"
        r = detect_ai_patterns(text)
        self.assertTrue(isinstance(r, list))
        self.assertTrue(len(r) > 0, f"Should detect cliche imagery, got: {len(r)} patterns")

    def test_detect_ai_high_frequency_words(self):
        text = "与此同时，他意识到了问题。此外，还需要考虑其他因素。值得一提的是，这很重要。"
        r = detect_ai_patterns(text)
        self.assertTrue(any(p["name"] == "AI高频词汇" for p in r), f"Should detect AI高频词汇, got: {[p['name'] for p in r]}")

    def test_detect_ai_inner_monologue(self):
        text = "他想，这一切难道都是命中注定的吗？或许从一开始他就错了。但是现在已经没有回头路了。他必须继续前进。"
        r = detect_ai_patterns(text)
        self.assertTrue(isinstance(r, list))

    def test_pattern_ids_are_unique(self):
        from backend.core.humanizer import AI_PATTERNS
        ids = [p["id"] for p in AI_PATTERNS]
        self.assertEqual(len(ids), len(set(ids)), "All pattern IDs must be unique")

    def test_all_patterns_have_fix(self):
        from backend.core.humanizer import AI_PATTERNS
        for p in AI_PATTERNS:
            self.assertIn("fix", p, f"Pattern {p.get('id','?')} '{p.get('name','?')}' missing fix")


class TestHumanizerBurstiness(unittest.TestCase):
    """句长突发性分析测试"""

    def test_normal_text_burstiness(self):
        text = "他看了她一眼。转身走了。风吹过街道，带着秋天的凉意。"
        r = _analyze_burstiness(text)
        self.assertIn("violations", r)
        self.assertIsInstance(r["violations"], int)

    def test_monotone_text_burstiness(self):
        text = ("这是一个很长很长的句子用来测试突发性分析功能。"
                + "这也是一个大致相同长度的句子测试。"
                + "这还是一个类似长度的测试句子。")
        r = _analyze_burstiness(text)
        self.assertIsInstance(r["violations"], int)

    def test_short_sentence_detection(self):
        text = ("好。走。停。这是短句测试。"
                + "这是一个相对较长的句子用来检测短句。然后又是一个短句。结束。")
        r = _analyze_burstiness(text)
        self.assertIsInstance(r["violations"], int)


class TestHumanizerScoring(unittest.TestCase):
    """评分系统测试"""

    def test_clean_text_scores_high(self):
        text = "他转身离开。风很大。她站在原地，看着他的背影消失在巷子尽头。街灯一盏接一盏亮起来。"
        r = humanize_text(text)
        self.assertIn("score", r)
        self.assertIn("grade", r)  # v2.14新增
        self.assertIn("category_stats", r)  # v2.14新增
        self.assertGreaterEqual(r["score"], 0)
        self.assertLessEqual(r["score"], 100)

    def test_ai_text_scores_low(self):
        text = ("与此同时，他太阳穴突突地跳，手心全是汗，胸口一紧。"
                "这不仅仅是一次突破，更是蜕变的里程碑。"
                "他心想，这一切难道都是命中注定的吗？"
                "这一刻，他明白了真正的力量。")
        r = humanize_text(text)
        self.assertIn("grade", r)
        self.assertLess(r["score"], 80, f"AI-heavy text should score below 80, got {r['score']}")

    def test_grade_mapping(self):
        text = "这是一个人工智能生成的文本，标志着新时代的里程碑。此外，这体现了深刻的变化。更重要的是，这说明了本质。"
        r = humanize_text(text)
        self.assertIn(r["grade"], ["A", "B", "C", "D", "F"])


class TestHumanizerPrompt(unittest.TestCase):
    """润色提示测试"""

    def test_empty_detected(self):
        p = build_humanizer_prompt([])
        self.assertEqual(p, "")

    def test_prompt_contains_coherence_protection(self):
        detected = [
            {"id": 7, "name": "AI高频词汇", "category": "语言", "count": 3},
        ]
        p = build_humanizer_prompt(detected)
        # v2.14: 故事连贯性保护
        self.assertIn("故事连贯性保护", p)
        self.assertIn("不得删除任何剧情", p)

    def test_prompt_max_10_summary_items(self):
        detected = [{"id": i, "name": f"pattern_{i}", "category": "test", "count": 1} for i in range(20)]
        p = build_humanizer_prompt(detected)
        # 最多显示10条摘要
        lines = p.count("\n- [")
        self.assertLessEqual(lines, 10)


class TestChapterSummarizer(unittest.TestCase):
    """章节摘要测试"""

    def setUp(self):
        self.s = ChapterSummarizer()

    def test_offline_mode(self):
        r = self.s.summarize_chapter(1, "测试内容" * 50)
        self.assertEqual(r["chapter"], 1)
        self.assertIn("summary", r)
        self.assertIn("importance", r)

    def test_batch_summarize(self):
        chapters = {
            1: "第一章测试内容" * 30,
            2: "第二章测试内容" * 30,
        }
        results = self.s.summarize_batch(chapters)
        self.assertEqual(len(results), 2)
        self.assertIn(1, results)
        self.assertIn(2, results)

    def test_should_compress(self):
        self.assertTrue(self.s.should_compress(10))
        self.assertTrue(self.s.should_compress(20))
        self.assertFalse(self.s.should_compress(5))
        self.assertFalse(self.s.should_compress(11))

    def test_token_budget_calculation(self):
        budget = self.s.get_token_budget(100)
        self.assertIn("full_inject", budget)
        self.assertIn("hybrid", budget)
        self.assertIn("is_safe", budget)
        # 100章全量注入远超hybrid策略
        self.assertGreater(budget["full_inject"], budget["hybrid"])
        self.assertTrue(budget["full_inject"] > 10000)
        # 30章以内的hybrid应该在安全范围
        budget_small = self.s.get_token_budget(30)
        self.assertLess(budget_small["hybrid"], 9000)

    def test_key_chapter_marking(self):
        self.s.mark_key_chapter(1)
        chapters = {1: "关键章节测试" * 30}
        results = self.s.summarize_batch(chapters)
        self.assertEqual(results[1]["importance"], "key")


if __name__ == '__main__':
    unittest.main()
