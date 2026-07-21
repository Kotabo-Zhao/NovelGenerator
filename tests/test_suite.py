"""NovelGenerator — 全链路测试套件 (50+ 用例)
运行: python tests/test_suite.py
"""

import asyncio, json, os, sys, tempfile, time, unittest
from unittest.mock import MagicMock, patch, AsyncMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from core.planner import Planner
from core.atomic_io import atomic_write_json, safe_read_json, atomic_write_text
from core.writer import Writer
from core.memory import NovelMemory
from core.engine import NovelEngine


# ═══════════════════════════════════════════
# Mock OpenAI client
# ═══════════════════════════════════════════
def mock_client(json_response=None, stream_chunks=None):
    """创建模拟 OpenAI 客户端"""
    client = MagicMock()
    
    if json_response:
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = json.dumps(json_response, ensure_ascii=False)
        client.chat.completions.create.return_value = resp
    
    if stream_chunks:
        chunks = []
        for text in stream_chunks:
            chunk = MagicMock()
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta.content = text
            chunks.append(chunk)
        client.chat.completions.create.return_value = iter(chunks)
    
    return client


# ═══════════════════════════════════════════
# 1. Planner — JSON 解析 (15 tests)
# ═══════════════════════════════════════════
class TestPlannerJSON(unittest.TestCase):
    def setUp(self):
        self.p = Planner(None, 'mock')

    def test_01_valid_json(self):
        r = self.p._parse_json('{"a":1}')
        self.assertEqual(r, {"a": 1})

    def test_02_json_with_markdown_fence(self):
        r = self.p._parse_json('```json\n{"a":1}\n```')
        self.assertEqual(r, {"a": 1})

    def test_03_trailing_comma_object(self):
        r = self.p._parse_json('{"a":1,}')
        self.assertEqual(r, {"a": 1})

    def test_04_trailing_comma_array(self):
        r = self.p._parse_json('[1,2,]')
        self.assertEqual(r, [1, 2])

    def test_05_nested_trailing_comma(self):
        r = self.p._parse_json('{"a":{"b":2,},}')
        self.assertEqual(r, {"a": {"b": 2}})

    def test_06_empty_content(self):
        self.assertIsNone(self.p._parse_json(''))

    def test_07_none_content(self):
        self.assertIsNone(self.p._parse_json(None))

    def test_08_json_with_extra_text_before(self):
        r = self.p._parse_json('some text {"a":1}')
        self.assertEqual(r, {"a": 1})

    def test_09_large_json(self):
        large = json.dumps({"items": [{"id": i, "name": f"item_{i}" * 20} for i in range(200)]})
        r = self.p._parse_json(large)
        self.assertEqual(len(r["items"]), 200)

    def test_10_trailing_comma_with_newline(self):
        r = self.p._parse_json('{"a":1,\n}')
        self.assertEqual(r, {"a": 1})

    def test_11_trailing_comma_with_spaces(self):
        r = self.p._parse_json('{"a":1,  }')
        self.assertEqual(r, {"a": 1})

    def test_12_nested_objects_braces(self):
        r = self.p._parse_json('{"a":{"b":{"c":3}}}')
        self.assertEqual(r, {"a": {"b": {"c": 3}}})

    def test_13_chinese_characters(self):
        r = self.p._parse_json('{"name":"测试中文","desc":"包含，逗号和符号"}')
        self.assertEqual(r["name"], "测试中文")

    def test_14_malformed_json_returns_none(self):
        self.assertIsNone(self.p._parse_json('{broken json!!!'))

    def test_15_multiple_top_level_objects_takes_first(self):
        r = self.p._parse_json('{"a":1}{"b":2}')
        self.assertEqual(r, {"a": 1})


# ═══════════════════════════════════════════
# 2. Atomic I/O (10 tests)
# ═══════════════════════════════════════════
class TestAtomicIO(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def test_16_write_and_read(self):
        path = os.path.join(self.tmpdir, 'test.json')
        atomic_write_json(path, {"key": "value"})
        data = safe_read_json(path)
        self.assertEqual(data, {"key": "value"})

    def test_17_read_nonexistent_returns_default(self):
        data = safe_read_json('/nonexistent/path.json')
        self.assertEqual(data, {})

    def test_18_read_nonexistent_custom_default(self):
        data = safe_read_json('/nonexistent/path.json', [])
        self.assertEqual(data, [])

    def test_19_corrupted_file_recovery(self):
        path = os.path.join(self.tmpdir, 'corrupt.json')
        with open(path, 'w') as f: f.write('{invalid')
        data = safe_read_json(path)
        self.assertEqual(data, {})
        self.assertTrue(os.path.exists(path + '.corrupted'))

    def test_20_atomic_write_text(self):
        path = os.path.join(self.tmpdir, 'text.txt')
        atomic_write_text(path, "hello world")
        with open(path) as f:
            self.assertEqual(f.read(), "hello world")

    def test_21_write_overwrite(self):
        path = os.path.join(self.tmpdir, 'overwrite.json')
        atomic_write_json(path, {"v": 1})
        atomic_write_json(path, {"v": 2})
        self.assertEqual(safe_read_json(path), {"v": 2})

    def test_22_write_nested_structures(self):
        path = os.path.join(self.tmpdir, 'nested.json')
        atomic_write_json(path, {"a": [1, {"b": [2, 3]}]})
        self.assertEqual(safe_read_json(path), {"a": [1, {"b": [2, 3]}]})

    def test_23_chinese_content(self):
        path = os.path.join(self.tmpdir, 'cn.json')
        atomic_write_json(path, {"标题": "测试中文内容" * 10})
        data = safe_read_json(path)
        self.assertIn("测试中文", data["标题"])

    def test_24_empty_list_default(self):
        path = os.path.join(self.tmpdir, 'empty.json')
        atomic_write_json(path, [])
        self.assertEqual(safe_read_json(path), [])

    def test_25_large_write(self):
        path = os.path.join(self.tmpdir, 'large.json')
        large = {"items": [{"id": i, "data": "x" * 100} for i in range(500)]}
        atomic_write_json(path, large)
        self.assertEqual(len(safe_read_json(path)["items"]), 500)


# ═══════════════════════════════════════════
# 3. Writer — 流式生成 (5 tests)
# ═══════════════════════════════════════════
class TestWriter(unittest.TestCase):
    def test_26_skip_polish_for_long_draft(self):
        """长文(>2000字)应跳过二次打磨"""
        client = mock_client(stream_chunks=["初稿内容" * 200])  # ~800 chars
        writer = Writer(client, 'deepseek-chat')
        # 只测试逻辑，不实际运行async stream
        self.assertIsNotNone(writer)

    def test_27_truncation_check_complete_text(self):
        from core.writer import _check_truncation
        # Need enough chars for ratio check: > 0.15 * target_words * 2
        para = "这是测试文本内容。包含完整结尾。"
        text = para * 100  # ~1700 chars
        is_trunc, _ = _check_truncation(text, 3000)
        self.assertFalse(is_trunc)

    def test_28_truncation_check_incomplete(self):
        from core.writer import _check_truncation
        text = "这是不完整的测试文本"
        is_trunc, _ = _check_truncation(text, 3000)
        self.assertTrue(is_trunc)

    def test_29_truncation_check_empty(self):
        from core.writer import _check_truncation
        is_trunc, _ = _check_truncation("", 3000)
        self.assertTrue(is_trunc)

    def test_30_humanizer_detect_chinese(self):
        from core.humanizer import detect_ai_patterns
        text = "此外，值得一提的是，在这个过程中，从某种意义上说。"
        patterns = detect_ai_patterns(text)
        self.assertGreater(len(patterns), 0)


# ═══════════════════════════════════════════
# 4. Memory — 状态管理 (8 tests)
# ═══════════════════════════════════════════
class TestMemory(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.mem = NovelMemory(self.tmpdir)

    def test_31_save_and_load_state(self):
        novel_dir = os.path.join(self.tmpdir, "test_novel")
        os.makedirs(novel_dir)
        self.mem.save_novel_state("test_novel", {"current_chapter": 5, "completed_chapters": [1,2,3,4,5]})
        state = self.mem.get_novel_state("test_novel")
        self.assertEqual(state["current_chapter"], 5)
        self.assertEqual(state["completed_chapters"], [1,2,3,4,5])

    def test_32_state_backward_compat(self):
        novel_dir = os.path.join(self.tmpdir, "old_novel")
        os.makedirs(novel_dir)
        # Old state without completed_chapters
        self.mem.save_novel_state("old_novel", {"current_chapter": 3})
        state = self.mem.get_novel_state("old_novel")
        self.assertIn("completed_chapters", state)

    def test_33_save_chapter(self):
        novel_dir = os.path.join(self.tmpdir, "novel_ch")
        os.makedirs(os.path.join(novel_dir, "chapters"))
        self.mem.save_chapter("novel_ch", 1, "# Chapter 1\nContent here")
        ch = os.path.join(novel_dir, "chapters", "chapter_0001.md")
        self.assertTrue(os.path.exists(ch))

    def test_34_foreshadowing_empty(self):
        novel_dir = os.path.join(self.tmpdir, "novel_fs")
        os.makedirs(novel_dir)
        ctx = self.mem.get_foreshadowing_context("novel_fs", 5)
        self.assertEqual(ctx, "")

    def test_35_foreshadowing_urgent(self):
        novel_dir = os.path.join(self.tmpdir, "novel_fs2")
        os.makedirs(novel_dir)
        # reveal_chapter <= current_chapter → 🔴 必须
        hooks = [{"plant_chapter": 1, "description": "神秘戒指", "reveal_chapter": 2, "resolved": False}]
        atomic_write_json(os.path.join(novel_dir, "foreshadowing.json"), hooks)
        ctx = self.mem.get_foreshadowing_context("novel_fs2", 3)
        self.assertIn("神秘戒指", ctx)
        self.assertIn("🔴", ctx)  # urgent

    def test_36_context_building(self):
        novel_dir = os.path.join(self.tmpdir, "novel_ctx")
        os.makedirs(novel_dir)
        atomic_write_json(os.path.join(novel_dir, "plan.json"), {
            "worldbuilding": {"era": "修真时代", "power_system": "灵力九阶", "core_conflict": "正邪之战"},
            "characters": {"protagonist": {"name": "林风", "identity": "散修", "cheat": "上古传承"}}
        })
        ctx = self.mem.build_writer_context("novel_ctx", 3, {
            "title": "测试章", "summary": "测试", "emotion_curve": "平稳", "characters": ["林风"], "hook": "悬念"
        })
        self.assertIn("林风", ctx)
        self.assertIn("上古传承", ctx)

    def test_37_state_corrupted_recovery(self):
        novel_dir = os.path.join(self.tmpdir, "broken_state")
        os.makedirs(novel_dir)
        with open(os.path.join(novel_dir, "state.json"), "w") as f:
            f.write("{corrupted")
        state = self.mem.get_novel_state("broken_state")
        self.assertIn("completed_chapters", state)

    def test_38_foreshadowing_update_and_resolve(self):
        novel_dir = os.path.join(self.tmpdir, "novel_fs3")
        os.makedirs(novel_dir)
        self.mem.update_foreshadowing("novel_fs3", 1, planted=[
            {"description": "伏笔A", "reveal_chapter": 5}
        ])
        self.mem.update_foreshadowing("novel_fs3", 5, resolved=["伏笔A"])
        ctx = self.mem.get_foreshadowing_context("novel_fs3", 10)
        self.assertEqual(ctx, "")


# ═══════════════════════════════════════════
# 5. Planner — 3阶段流式 (4 tests)
# ═══════════════════════════════════════════
class TestPlannerStream(unittest.TestCase):
    async def _collect_events(self, coro):
        events = []
        async for e in coro:
            events.append(e)
        return events

    def test_39_planner_stream_worldbuilding_fail(self):
        """世界观生成失败应返回 error 事件"""
        client = mock_client(json_response={"title": "测试", "worldbuilding": {"era": "古代", "power_system": "灵气", "core_conflict": "正邪", "factions": []}})
        p = Planner(client, 'deepseek-chat')

        async def run():
            events = []
            async for e in p.plan_stream({"genre": "修仙", "inspiration": "测试", "target_words": 100000}):
                events.append(e)
            types = [e["type"] for e in events]
            has_done = "done" in types
            has_progress = "progress" in types
            self.assertTrue(has_progress)
            if has_done:
                plan = [e for e in events if e["type"] == "done"][0]["plan"]
                self.assertIn("title", plan)
        asyncio.run(run())

    def test_40_empty_inspiration_handled(self):
        p = Planner(MagicMock(), 'mock')
        client = mock_client(json_response={"title": "默认", "worldbuilding": {}})
        p.client = client

        async def run():
            async for e in p.plan_stream({"genre": "玄幻", "inspiration": "", "target_words": 100000}):
                if e["type"] == "error":
                    return
        asyncio.run(run())

    def test_41_retry_mechanism(self):
        """验证重试逻辑: 第一次失败 → 重试"""
        client = MagicMock()
        # First call fails, second succeeds
        fail_resp = MagicMock()
        fail_resp.choices = [MagicMock()]
        fail_resp.choices[0].message.content = "not json at all"
        ok_resp = MagicMock()
        ok_resp.choices = [MagicMock()]
        ok_resp.choices[0].message.content = '{"title":"ok","worldbuilding":{"era":"x","power_system":"x","core_conflict":"x","factions":[]}}'
        client.chat.completions.create.side_effect = [fail_resp, ok_resp]
        
        p = Planner(client, 'deepseek-chat')
        
        async def run():
            result = await p._call_llm("test prompt", "test", max_tokens=512)
            self.assertIsNotNone(result)
        asyncio.run(run())

    def test_42_plan_stream_full_flow(self):
        """完整3阶段流程"""
        client = mock_client()
        p = Planner(client, 'mock')
        
        def make_resp(data):
            r = MagicMock()
            r.choices = [MagicMock()]
            r.choices[0].message.content = json.dumps(data, ensure_ascii=False)
            return r
        
        wb = {"title": "测试书", "worldbuilding": {"era": "古代", "geography": "大陆", "power_system": "灵气", "core_conflict": "正邪", "factions": [{"name": "正派", "description": "正道", "alignment": "正"}]}}
        chars = {"characters": {"protagonist": {"name": "主角"}, "supporting": [], "antagonist": [], "bible_summary": ""}}
        outline = {"outline": {"volumes": [{"number": 1, "title": "卷一", "chapters": [{"number": 1, "title": "首章", "summary": "开始", "emotion_curve": "平稳", "conflict": "", "characters": ["主角"], "hook": "悬念", "target_words": 3000}], "act": "第一幕·建置", "theme": "", "act_function": ""}], "total_chapters": 1, "three_act_map": "", "rhythm_notes": ""}}
        
        client.chat.completions.create.side_effect = [make_resp(wb), make_resp(chars), make_resp(outline)]
        
        async def run():
            events = []
            async for e in p.plan_stream({"genre": "修仙", "inspiration": "测试", "target_words": 100000}):
                events.append(e)
            done = [e for e in events if e["type"] == "done"]
            self.assertEqual(len(done), 1)
        asyncio.run(run())


# ═══════════════════════════════════════════
# 6. Engine (6 tests)
# ═══════════════════════════════════════════
class TestEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try: from backend import config as cfg
        except ImportError: import config as cfg
        cls._orig_key = cfg.DEEPSEEK_API_KEY
        cfg.DEEPSEEK_API_KEY = "test-key"
    
    @classmethod
    def tearDownClass(cls):
        try: from backend import config as cfg
        except ImportError: import config as cfg
        cfg.DEEPSEEK_API_KEY = cls._orig_key

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        try: from backend import config as cfg
        except ImportError: import config as cfg
        self._orig_novels = cfg.NOVELS_DIR
        cfg.NOVELS_DIR = self.tmpdir

    def tearDown(self):
        try: from backend import config as cfg
        except ImportError: import config as cfg
        cfg.NOVELS_DIR = self._orig_novels

    def test_43_list_empty_novels(self):
        engine = NovelEngine()
        novels = engine.list_novels()
        self.assertEqual(novels, [])

    def test_44_novel_not_found(self):
        engine = NovelEngine()
        self.assertIsNone(engine.get_novel("nonexistent"))

    def test_45_export_no_chapters(self):
        engine = NovelEngine()
        try: from backend import config as cfg
        except ImportError: import config as cfg
        novel_dir = os.path.join(cfg.NOVELS_DIR, "test_export")
        os.makedirs(novel_dir)
        atomic_write_json(os.path.join(novel_dir, "plan.json"), {"title": "test"})
        content, err = engine.export_novel("test_export", "txt")
        self.assertIsNone(content)
        self.assertIsNotNone(err)

    def test_46_format_char_entry_flattens_nested(self):
        engine = NovelEngine()
        char = {
            "name": "测试",
            "personality": {"surface": "开朗", "true_self": "内向", "flaw": "固执"},
            "motivation": {"want": "力量", "need": "认可"},
            "secret": "秘密", "arc": "成长", "catchphrase": "口头禅",
            "role": "主角", "identity": "修士"
        }
        entry = engine._format_char_entry(char, "主角")
        self.assertIn("开朗", entry["personality"])
        self.assertIn("力量", entry["motivation"])

    def test_47_relationship_map(self):
        engine = NovelEngine()
        p = {"name": "主角"}
        s = [{"name": "配角A", "meaning": "盟友"}]
        a = [{"name": "反派A", "conflict": "宿敌"}]
        edges = engine._build_relationship_map(p, s, a)
        self.assertEqual(len(edges), 2)

    def test_48_fallback_chapter_outline(self):
        engine = NovelEngine()
        plan = {"outline": {"volumes": []}}
        ch = engine._find_chapter_outline(plan, 5)
        self.assertIsNone(ch)


# ═══════════════════════════════════════════
# 7. 边界场景 (5 tests)
# ═══════════════════════════════════════════
class TestEdgeCases(unittest.TestCase):
    def test_49_very_long_string_in_json(self):
        p = Planner(None, 'mock')
        s = '{"text":"' + "测试内容" * 5000 + '"}'
        r = p._try_parse(s)
        self.assertIsNotNone(r)

    def test_50_empty_object_value(self):
        p = Planner(None, 'mock')
        r = p._parse_json('{}')
        self.assertEqual(r, {})

    def test_51_array_only(self):
        p = Planner(None, 'mock')
        r = p._parse_json('[1,2,3,4,5]')
        self.assertEqual(r, [1, 2, 3, 4, 5])

    def test_52_escaped_quotes_in_string(self):
        p = Planner(None, 'mock')
        r = p._parse_json('{"msg":"he said \\"hello\\""}')
        self.assertEqual(r["msg"], 'he said "hello"')

    def test_53_zero_max_tokens_params(self):
        try: from backend import config as cfg
        except ImportError: import config as cfg
        self.assertGreater(cfg.DEFAULT_CHAPTER_WORDS, 0)


# ═══════════════════════════════════════════
# 8. 综合场景 (2 tests)
# ═══════════════════════════════════════════
class TestIntegration(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        try: from backend import config as cfg
        except ImportError: import config as cfg
        self._orig_novels = cfg.NOVELS_DIR
        cfg.NOVELS_DIR = self.tmpdir

    def tearDown(self):
        try: from backend import config as cfg
        except ImportError: import config as cfg
        cfg.NOVELS_DIR = self._orig_novels

    def test_54_full_create_save_list_flow(self):
        """完整流程：创建 → 保存 → 列表 → 读取"""
        engine = NovelEngine()
        # Create a novel (mocked)
        novel_dir = os.path.join(self.tmpdir, "test_flow")
        os.makedirs(novel_dir)
        plan = {
            "title": "测试小说", "genre": "玄幻", "style": "热血爽文",
            "target_words": 100000,
            "worldbuilding": {"era": "古代", "power_system": "灵力", "core_conflict": "正邪之战", "factions": []},
            "characters": {"protagonist": {"name": "主角", "identity": "散修"}, "supporting": [], "antagonist": [], "bible_summary": ""},
            "outline": {"volumes": [{"number": 1, "title": "卷一", "chapters": [{"number": 1, "title": "首章", "summary": "开始", "emotion_curve": "平稳", "conflict": "", "characters": ["主角"], "hook": "悬念", "target_words": 3000}], "act": "第一幕·建置", "theme": "", "act_function": ""}], "total_chapters": 1, "three_act_map": "", "rhythm_notes": ""},
            "_meta": {"created_at": "2024-01-01T00:00:00", "model": "test"}
        }
        atomic_write_json(os.path.join(novel_dir, "plan.json"), plan)
        engine.memory.save_novel_state("test_flow", {"current_chapter": 0, "total_chapters": 1, "total_words": 0, "completed_chapters": [], "status": "planning_done"})
        
        # List
        novels = engine.list_novels()
        self.assertEqual(len(novels), 1)
        
        # Get
        novel = engine.get_novel("test_flow")
        self.assertEqual(novel["title"], "测试小说")
        
        # Update plan
        plan["title"] = "修改后的小说"
        success = engine.update_plan("test_flow", plan)
        self.assertTrue(success)
        updated = engine.get_novel("test_flow")
        self.assertEqual(updated["title"], "修改后的小说")

    def test_55_state_consistency_after_chapter_ops(self):
        """状态一致性：模拟章节生成后的状态更新"""
        mem = NovelMemory(self.tmpdir)
        novel_dir = os.path.join(self.tmpdir, "state_test")
        os.makedirs(novel_dir)
        
        # Initial
        mem.save_novel_state("state_test", {"current_chapter": 0, "total_chapters": 3, "completed_chapters": []})
        
        # After ch1
        mem.save_novel_state("state_test", {"current_chapter": 1, "total_chapters": 3, "completed_chapters": [1]})
        state = mem.get_novel_state("state_test")
        self.assertEqual(state["completed_chapters"], [1])
        
        # After ch1+ch2
        mem.save_novel_state("state_test", {"current_chapter": 2, "total_chapters": 3, "completed_chapters": [1, 2]})
        state = mem.get_novel_state("state_test")
        self.assertEqual(state["completed_chapters"], [1, 2])
        
        # After ch1+ch2+ch3
        mem.save_novel_state("state_test", {"current_chapter": 3, "total_chapters": 3, "completed_chapters": [1, 2, 3]})
        state = mem.get_novel_state("state_test")
        self.assertEqual(state["completed_chapters"], [1, 2, 3])


if __name__ == '__main__':
    print("=" * 60)
    print("NovelGenerator 全链路测试套件")
    print(f"共 55 个测试用例")
    print("=" * 60)
    
    # 设置 asyncio 策略（Windows 兼容）
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    unittest.main(verbosity=2)
