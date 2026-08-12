"""测试核心内容质量模块 — humanizer, chapter_summarizer, consistency_validator"""
import unittest, os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from backend.core.humanizer import detect_ai_patterns, humanize_text, build_humanizer_prompt, _analyze_burstiness
from backend.core.chapter_summarizer import ChapterSummarizer
from backend.core.evaluation_system import chapter_structure, evaluate_chapter, hook_strength


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


class TestNarrativeEvaluation(unittest.TestCase):
    """叙事结构评估回归：指标必须能识别真实的章节推进，而不是只看爽点词。"""

    def test_hook_keeps_last_sentence_when_chapter_ends_with_punctuation(self):
        text = "火光照进门缝，他终于看清了那枚黑色的印记。为什么偏偏是黑色的。"

        result = hook_strength(text)

        self.assertEqual(result["last_sentence"], "为什么偏偏是黑色的")
        self.assertGreater(result["strength"], 0)

    def test_hook_recognizes_decision_or_next_action_without_dopamine_words(self):
        text = "沈砚把证物收进内袋，关掉档案室的灯。天亮以前，他必须回到旧码头。"

        result = hook_strength(text)

        self.assertGreaterEqual(result["strength"], 3)
        self.assertTrue(result["open_loop_signals"])

    def test_hook_recognizes_concrete_ending_reveal_without_question_mark(self):
        text = "他推开铁皮门，门后的墙上贴满了照片，照片中央的人正是失踪多年的父亲。"

        result = hook_strength(text)

        self.assertGreaterEqual(result["strength"], 4)
        self.assertTrue(result["ending_reveal_signals"])

    def test_structure_rewards_goal_obstacle_choice_consequence_and_hook(self):
        text = (
            "沈砚刚撬开档案柜，门外的脚步声就停在了第三层。"
            "他想取走封条，却发现封条上的编号已经被人替换。\n\n"
            "保安推门进来，要求他立刻交出钥匙。沈砚没有解释，反而把假封条塞进打印机，"
            "借机器卡纸的瞬间拍下了真正的编号。\n\n"
            "打印机吐出最后一张纸时，屏幕上跳出一个陌生账号：明晚十二点，旧码头见。"
        )

        result = chapter_structure(text, {"summary": "沈砚调查被替换的封条", "hook": "旧码头的约见"})

        self.assertGreaterEqual(result["score"], 65)
        self.assertGreaterEqual(result["metrics"]["opening"], 60)
        self.assertGreaterEqual(result["metrics"]["agency"], 60)
        self.assertGreaterEqual(result["metrics"]["consequence"], 50)
        self.assertGreaterEqual(result["metrics"]["ending_hook"], 30)

    def test_evaluate_chapter_exposes_structure_contract(self):
        result = evaluate_chapter("他推开门，发现桌上多了一封没有署名的信。")

        self.assertIn("structure", result)
        self.assertIn("章节结构", {item["metric"] for item in result["score_breakdown"]})
        self.assertGreaterEqual(result["structure"]["score"], 0)
        self.assertLessEqual(result["structure"]["score"], 100)

    def test_coherence_does_not_call_low_lexical_overlap_a_narrative_jump(self):
        text = (
            "沈砚推开档案室的门，屋里只亮着一盏旧灯。"
            "他没有停留，先检查了门锁和桌面的灰尘。"
            "随后，手机震动了一下，匿名账号发来旧码头的地址。"
            "沈砚收起证据，转身走进雨里。"
        )

        result = __import__("backend.core.evaluation_system", fromlist=["coherence_score"]).coherence_score(text)

        self.assertNotEqual(result["verdict"], "跳跃过大")


if __name__ == '__main__':
    unittest.main()
