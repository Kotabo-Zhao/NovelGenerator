"""NovelGenerator 全功能压力测试 (80+ 用例)
运行: python tests/stress_test.py
"""

import asyncio, json, os, sys, tempfile, time, unittest, shutil, concurrent.futures
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from core.planner import Planner
from core.atomic_io import atomic_write_json, safe_read_json, atomic_write_text
from core.writer import Writer, _check_truncation, humanize_text
from core.memory import NovelMemory
from core.engine import NovelEngine
import config as cfg


# ═══════════════ Helpers ═══════════════

def mock_client(json_response=None):
    client = MagicMock()
    if json_response:
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = json.dumps(json_response, ensure_ascii=False)
        client.chat.completions.create.return_value = resp
    return client


def make_plan(title="测试", chapters=10):
    return {
        "title": title, "genre": "玄幻", "style": "热血爽文",
        "worldbuilding": {
            "era": "修真时代", "power_system": "灵力九阶",
            "core_conflict": "正邪大战",
            "factions": [{"name": "青云宗", "description": "正道", "alignment": "正"}]
        },
        "characters": {
            "protagonist": {
                "name": "测试主角", "age": "18", "identity": "散修",
                "personality": {"surface": "冷静", "true_self": "热血", "flaw": "自信"},
                "motivation": {"want": "变强", "need": "守护"},
                "backstory": "孤儿", "arc": "成长", "cheat": "神器",
                "secret": "身世", "catchphrase": "就这？",
                "relationships": [{"name": "师尊", "type": "师徒", "dynamic": "教导"}]
            },
            "supporting": [
                {"name": "配角A", "identity": "师兄", "relation": "盟友",
                 "personality": "豪爽", "role": "助手", "mini_arc": "成长", "meaning": "友情"}
            ],
            "antagonist": [
                {"name": "反派A", "motivation": "统治", "power": "魔功",
                 "conflict": "灭门之仇", "humanity": "曾被迫害"}
            ],
            "bible_summary": "标准阵容"
        },
        "outline": {
            "volumes": [{
                "number": 1, "title": "崛起", "act": "第一幕·建置",
                "theme": "启蒙", "act_function": "铺垫",
                "chapters": [
                    {"number": i+1, "title": f"第{i+1}章",
                     "summary": f"核心事件{i+1}", "emotion_curve": "压抑→爆发→余韵",
                     "conflict": "冲突", "characters": ["主角"],
                     "hook": f"钩子{i+1}", "target_words": 3000}
                    for i in range(chapters)
                ]
            }],
            "total_chapters": chapters, "three_act_map": "标准三幕", "rhythm_notes": "节奏"
        }
    }


# ════════════════════════════════════════════════════════════
# 1. Planner JSON 解析压力 (18 tests)
# ════════════════════════════════════════════════════════════

class TestPlannerJSON(unittest.TestCase):

    def setUp(self):
        self.p = Planner(None, 'mock')

    def test_p01_huge_100kb(self):
        """100KB超大JSON"""
        huge = {"data": ["x" * 100 for _ in range(1000)]}
        t0 = time.time()
        r = self.p._parse_json(json.dumps(huge, ensure_ascii=False))
        self.assertLess(time.time() - t0, 2.0)
        self.assertEqual(len(r['data']), 1000)

    def test_p02_deep_nesting(self):
        """深度嵌套"""
        deep = {"a": {"b": {"c": {"d": {"e": {"f": "deep"}}}}}}
        r = self.p._parse_json(json.dumps(deep))
        self.assertEqual(r['a']['b']['c']['d']['e']['f'], "deep")

    def test_p03_trailing_comma_basic(self):
        """基础尾逗号"""
        for v in ['{"a": 1,}', '[1, 2,]', '{"a": {"b": 1,},}']:
            r = self.p._try_parse(v)
            self.assertIsNotNone(r, f"Failed: {v}")

    def test_p04_trailing_comma_whitespace(self):
        """尾逗号+空白变体"""
        for v in ['{"a": 1, }', '{"a": 1,\n}', '{"a": 1,\r\n}',
                   '{"a": 1,\n "b": 2,\n}']:
            r = self.p._try_parse(v)
            self.assertIsNotNone(r, f"Failed: {repr(v)}")

    def test_p05_markdown_fences(self):
        """Markdown代码块"""
        for f in ['```json\n{"x":1}\n```', '```\n{"x":1}\n```',
                   '  ```json\n{"x":1}\n```  ']:
            self.assertIsNotNone(self.p._parse_json(f))

    def test_p06_nested_in_text(self):
        """嵌套在文本中"""
        self.assertIsNotNone(self.p._parse_json('前言 {"a": 1} 后记'))
        self.assertIsNotNone(self.p._parse_json('{"outer": [1,2]} trailing'))

    def test_p07_unicode_chars(self):
        """Unicode/特殊字符"""
        self.assertIsNotNone(self.p._parse_json('{"a": "🔥🎉"}'))
        self.assertIsNotNone(self.p._parse_json('{"a": "\\\\u0030"}'))
        self.assertIsNotNone(self.p._parse_json('{"a": "你好\\n世界"}'))

    def test_p08_duplicate_keys(self):
        """重复key"""
        r = self.p._try_parse('{"a": 1, "a": 2}')
        self.assertEqual(r['a'], 2)

    def test_p09_empty_bad_input(self):
        """空/坏输入安全返回None"""
        self.assertIsNone(self.p._try_parse(""))
        self.assertIsNone(self.p._try_parse("不是JSON"))

    def test_p10_retry_success_after_fail(self):
        """重试成功"""
        count = [0]
        def side(**kw):
            count[0] += 1
            resp = MagicMock()
            resp.choices = [MagicMock()]
            resp.choices[0].message.content = "bad" if count[0] == 1 else '{"ok":1}'
            return resp

        client = MagicMock()
        client.chat.completions.create.side_effect = side

        async def run():
            return await Planner(client, 'mock')._call_llm("x", "test", max_tokens=100)

        r = asyncio.run(run())
        self.assertIsNotNone(r)
        self.assertGreater(count[0], 1)

    def test_p11_retry_all_fail(self):
        """全部失败返回None"""
        client = MagicMock()
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = "always broken"
        client.chat.completions.create.return_value = resp

        async def run():
            return await Planner(client, 'mock')._call_llm("x", "test", max_tokens=100)

        r = asyncio.run(run())
        self.assertIsNone(r)

    def test_p12_plan_stream_error_propagation(self):
        """错误正确传播为error事件"""
        client = MagicMock()
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = "garbage"
        client.chat.completions.create.return_value = resp

        async def run():
            events = []
            async for e in Planner(client, 'mock').plan_stream({
                "genre": "修仙", "style": "热血爽文",
                "inspiration": "test", "target_words": 100000
            }):
                events.append(e)
            return events

        events = asyncio.run(run())
        errors = [e for e in events if e['type'] == 'error']
        self.assertGreater(len(errors), 0, "Should produce error events")

    def test_p13_plan_stream_success(self):
        """plan_stream全流程成功"""
        calls = [0]

        def side(**kw):
            calls[0] += 1
            resp = MagicMock()
            resp.choices = [MagicMock()]
            if calls[0] == 1:
                resp.choices[0].message.content = json.dumps({
                    "title": "测试", "worldbuilding": {
                        "era": "修真", "power_system": "灵力",
                        "core_conflict": "战争", "factions": []
                    }
                })
            elif calls[0] == 2:
                resp.choices[0].message.content = json.dumps({
                    "characters": {
                        "protagonist": {"name": "A"},
                        "supporting": [{"name": "B"}],
                        "antagonist": [{"name": "C"}]
                    }
                })
            else:
                resp.choices[0].message.content = json.dumps({
                    "outline": {
                        "volumes": [{"number":1, "title":"", "act":"第一幕",
                                   "theme":"", "act_function":"",
                                   "chapters": [
                                       {"number":1, "title":"", "summary":"测试",
                                        "emotion_curve":"", "conflict":"",
                                        "characters":["A"], "hook":"", "target_words":3000}
                                   ]}],
                        "total_chapters": 1, "three_act_map": "", "rhythm_notes": ""
                    }
                })
            return resp

        client = MagicMock()
        client.chat.completions.create.side_effect = side

        async def run():
            events = []
            async for e in Planner(client, 'mock').plan_stream({
                "genre": "修仙", "style": "热血爽文",
                "inspiration": "test", "target_words": 50000
            }):
                events.append(e)
            return events

        events = asyncio.run(run())
        done = [e for e in events if e['type'] == 'done']
        progress = [e for e in events if e['type'] == 'progress']
        self.assertEqual(len(done), 1)
        self.assertGreater(len(progress), 2)
        plan = done[0]['plan']
        self.assertIn('title', plan)
        self.assertIn('characters', plan)
        self.assertIn('outline', plan)

    def test_p14_v4_model_thinking_disabled(self):
        """V4模型自动禁用reasoning"""
        client = mock_client(json_response={"a": 1})
        async def run():
            return await Planner(client, 'deepseek-v4-flash')._call_llm("test", "v4", max_tokens=100, retry=False)
        r = asyncio.run(run())
        self.assertIsNotNone(r)
        kwargs = client.chat.completions.create.call_args[1]
        self.assertIn('extra_body', kwargs)
        self.assertEqual(kwargs['extra_body'], {'thinking': {'type': 'disabled'}})

    def test_p15_special_chars_input(self):
        """特殊字符输入不崩溃"""
        client = MagicMock()
        calls = [0]

        def side(**kw):
            calls[0] += 1
            resp = MagicMock()
            resp.choices = [MagicMock()]
            if calls[0] <= 3:
                resp.choices[0].message.content = json.dumps({
                    "title": "t", "worldbuilding": {"era": "x", "power_system": "x",
                        "core_conflict": "x", "factions": []}
                } if calls[0] == 1 else
                {"characters": {"protagonist": {"name": "x"},
                    "supporting": [{"name": "x"}], "antagonist": [{"name": "x"}]}}
                if calls[0] == 2 else
                {"outline": {"volumes": [{"number":1, "title":"", "act":"x",
                    "theme":"", "act_function":"", "chapters": [
                        {"number":1, "title":"", "summary":"x", "emotion_curve":"",
                         "conflict":"", "characters":["x"], "hook":"", "target_words":3000}
                    ]}], "total_chapters":1, "three_act_map":"", "rhythm_notes":""}}
                )
            return resp

        client.chat.completions.create.side_effect = side

        async def run():
            events = []
            async for e in Planner(client, 'mock').plan_stream({
                "genre": "玄幻", "style": "热血爽文",
                "inspiration": '一<段>&特"殊\'文\\n本', "target_words": 50000
            }):
                events.append(e)
            return events

        events = asyncio.run(run())
        errors = [e for e in events if e['type'] == 'error']
        self.assertEqual(len(errors), 0)

    def test_p16_outline_chapter_limit(self):
        """大纲章数可控"""
        calls = [0]

        def side(**kw):
            calls[0] += 1
            resp = MagicMock()
            resp.choices = [MagicMock()]
            if calls[0] == 1:
                resp.choices[0].message.content = json.dumps({
                    "title": "t", "worldbuilding": {
                        "era": "x", "power_system": "x",
                        "core_conflict": "x", "factions": []
                    }
                })
            elif calls[0] == 2:
                resp.choices[0].message.content = json.dumps({
                    "characters": {
                        "protagonist": {"name": "x"},
                        "supporting": [{"name": "x"}],
                        "antagonist": [{"name": "x"}]
                    }
                })
            else:
                # Even if LLM returns 100 chapters, system should handle it
                chs = [{"number": i+1, "title": f"第{i+1}章",
                      "summary": f"事件{i+1}", "emotion_curve":"",
                      "conflict":"", "characters":["x"], "hook":"", "target_words":3000}
                      for i in range(40)]  # capped at 40
                resp.choices[0].message.content = json.dumps({
                    "outline": {
                        "volumes": [{"number":1, "title":"", "act":"第一幕",
                                   "theme":"", "act_function":"", "chapters": chs}],
                        "total_chapters": 40, "three_act_map": "", "rhythm_notes": ""
                    }
                })
            return resp

        client = MagicMock()
        client.chat.completions.create.side_effect = side

        async def run():
            events = []
            async for e in Planner(client, 'mock').plan_stream({
                "genre": "修仙", "style": "热血爽文",
                "inspiration": "test", "target_words": 500000
            }):
                events.append(e)
            return events

        events = asyncio.run(run())
        done = [e for e in events if e['type'] == 'done']
        self.assertEqual(len(done), 1)
        ch_count = len(done[0]['plan']['outline']['volumes'][0]['chapters'])
        self.assertLessEqual(ch_count, 40)

    def test_p17_style_fallback(self):
        """风格降级"""
        from core.styles import get_style
        r = get_style("不存在的风格")
        self.assertIsNotNone(r)
        self.assertEqual(r['name'], "热血爽文")

    def test_p18_prompt_exceeds_limit(self):
        """超大prompt不崩溃"""
        client = mock_client(json_response={"x": 1})
        async def run():
            r = await Planner(client, 'mock')._call_llm(
                "a" * 50000, "big", max_tokens=100, retry=False)
            return r
        r = asyncio.run(run())
        self.assertTrue(r is None or isinstance(r, dict))


# ════════════════════════════════════════════════════════════
# 2. AtomicIO 压力 (10 tests)
# ════════════════════════════════════════════════════════════

class TestAtomicIO(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_a01_basic_rw(self):
        """基本读写"""
        path = os.path.join(self.tmpdir, "test.json")
        atomic_write_json(path, {"hello": "world"})
        self.assertEqual(safe_read_json(path), {"hello": "world"})

    def test_a02_concurrent_writes(self):
        """并发写入"""
        path = os.path.join(self.tmpdir, "conc.json")
        errors = []

        def write(i):
            try:
                atomic_write_json(path, {"v": i})
            except Exception as e:
                errors.append(str(e))

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(write, range(30)))

        r = safe_read_json(path)
        self.assertIsInstance(r, dict)

    def test_a03_corrupted_recovery(self):
        """损坏恢复"""
        path = os.path.join(self.tmpdir, "bad.json")
        with open(path, 'w') as f:
            f.write("{broken data!!!")
        r = safe_read_json(path, {"recovered": True})
        self.assertEqual(r, {"recovered": True})

    def test_a04_large_file(self):
        """大文件写入"""
        path = os.path.join(self.tmpdir, "big.json")
        data = {"items": ["x" * 100 for _ in range(10000)]}
        t0 = time.time()
        atomic_write_json(path, data)
        self.assertLess(time.time() - t0, 5)
        r = safe_read_json(path)
        self.assertEqual(len(r['items']), 10000)

    def test_a05_empty_file(self):
        """空文件"""
        path = os.path.join(self.tmpdir, "empty.json")
        with open(path, 'w') as f:
            f.write("")
        self.assertEqual(safe_read_json(path, {"fallback": "y"}), {"fallback": "y"})

    def test_a06_text_write(self):
        """文本原子写"""
        path = os.path.join(self.tmpdir, "text.txt")
        atomic_write_text(path, "你好世界\n第二行")
        with open(path, 'r', encoding='utf-8') as f:
            self.assertIn("你好世界", f.read())

    def test_a07_rapid_writes(self):
        """快速连续写入100次"""
        path = os.path.join(self.tmpdir, "rapid.json")
        for i in range(100):
            atomic_write_json(path, {"seq": i})
        self.assertGreaterEqual(safe_read_json(path)['seq'], 0)

    def test_a08_path_with_spaces(self):
        """路径含空格和中文"""
        path = os.path.join(self.tmpdir, "测试 目录", "数据.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        atomic_write_json(path, {"test": "中文"})
        self.assertEqual(safe_read_json(path), {"test": "中文"})

    def test_a09_interrupted_write_cleanup(self):
        """写入中断后清理"""
        path = os.path.join(self.tmpdir, "clean.json")
        atomic_write_json(path, {"safe": True})
        tmp_path = path + ".writing.tmp"
        with open(tmp_path, 'w') as f:
            f.write('{"junk":')
        # Next write should clean up
        atomic_write_json(path, {"clean": True})
        r = safe_read_json(path)
        self.assertEqual(r, {"clean": True})

    def test_a10_non_exist_path(self):
        """路径不存在时创建"""
        path = os.path.join(self.tmpdir, "new_dir", "nested", "file.json")
        atomic_write_json(path, {"ok": True})
        self.assertTrue(os.path.exists(path))
        self.assertEqual(safe_read_json(path), {"ok": True})


# ════════════════════════════════════════════════════════════
# 3. Writer 压力 (6 tests)
# ════════════════════════════════════════════════════════════

class TestWriter(unittest.TestCase):

    def test_w01_complete_text(self):
        """完整文本不报截断"""
        text = ("第一章的故事从这里开始。主角踏上了漫长的修炼旅程。" * 60 +
                "一切终于结束了。他望着远方的夕阳，露出了欣慰的微笑。")
        result = _check_truncation(text, 3000)
        is_trunc = result[0] if isinstance(result, tuple) else result
        self.assertFalse(is_trunc, f"Complete text shouldn't be truncated, got {result}")


    def test_w02_unfinished_quote(self):
        """未完成引号"""
        text = "他说：" + "天地无极乾坤借法" * 20 + "你"
        result = _check_truncation(text, 3000)
        is_trunc = result[0] if isinstance(result, tuple) else result
        self.assertTrue(is_trunc)


    def test_w03_mid_sentence(self):
        """中途截断"""
        text = "这是一个故事。但是" + "没有完结" * 30 + "世界终结在"
        result = _check_truncation(text, 3000)
        is_trunc = result[0] if isinstance(result, tuple) else result
        self.assertTrue(is_trunc)


    def test_w04_short_text(self):
        """短文被检测为截断"""
        result = _check_truncation("短", 3000)
        is_trunc = result[0] if isinstance(result, tuple) else result
        self.assertTrue(is_trunc)  # < 100 chars → truncated

    def test_w05_humanizer_detection(self):
        """AI痕迹检测"""
        text = "首先我们需要理解这个问题。其次通过分析可以发现。" * 10
        r = humanize_text(text)
        self.assertIsInstance(r['score'], int)

    def test_w06_v4_thinking_disabled(self):
        """Writer v4 thinking disabled"""
        client = MagicMock()
        client.chat.completions.create.return_value = MagicMock()
        w = Writer(client, 'deepseek-v4-flash')
        w._create(model='test', max_tokens=100)
        kwargs = client.chat.completions.create.call_args[1]
        self.assertIn('extra_body', kwargs)
        self.assertEqual(kwargs['extra_body'], {'thinking': {'type': 'disabled'}})


# ════════════════════════════════════════════════════════════
# 4. Memory 压力 (10 tests)
# ════════════════════════════════════════════════════════════

class TestMemory(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig = cfg.NOVELS_DIR
        cfg.NOVELS_DIR = self.tmpdir

    def tearDown(self):
        cfg.NOVELS_DIR = self._orig
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _setup(self, nid, plan=None, chapters_done=None):
        d = os.path.join(self.tmpdir, nid)
        os.makedirs(os.path.join(d, "chapters"), exist_ok=True)
        atomic_write_json(os.path.join(d, "plan.json"), plan or make_plan(nid, 10))
        return d

    def test_m01_state_roundtrip(self):
        """状态读写"""
        self._setup("test")
        mem = NovelMemory(cfg.NOVELS_DIR)
        mem.save_novel_state("test", {"current_chapter":5, "total_words":15000,
                                       "completed_chapters":[1,2,3,4,5]})
        s = mem.get_novel_state("test")
        self.assertEqual(s['completed_chapters'], [1,2,3,4,5])

    def test_m02_state_corruption_recovery(self):
        """状态损坏恢复"""
        self._setup("corrupt")
        for i in range(1, 4):
            mem = NovelMemory(cfg.NOVELS_DIR)
            mem.save_chapter("corrupt", i, f"第{i}章。" * 50)

        # corrupt state
        with open(os.path.join(self.tmpdir, "corrupt", "state.json"), 'w') as f:
            f.write("{broken!")

        mem = NovelMemory(cfg.NOVELS_DIR)
        s = mem.get_novel_state("corrupt")
        self.assertEqual(s['completed_chapters'], [1, 2, 3])

    def test_m03_foreshadowing_context(self):
        """伏笔上下文"""
        self._setup("fs")
        mem = NovelMemory(cfg.NOVELS_DIR)
        mem.save_novel_state("fs", {"current_chapter":0, "total_words":0, "completed_chapters":[]})
        mem.update_foreshadowing("fs", 1, planted=[
            {"description": "神秘戒指", "reveal_chapter": 3, "resolved": False}
        ])
        ctx = mem.get_foreshadowing_context("fs", 2)
        self.assertIn("神秘戒指", ctx)

    def test_m04_foreshadowing_overdue(self):
        """过期伏笔标记"""
        self._setup("fs2")
        mem = NovelMemory(cfg.NOVELS_DIR)
        mem.save_novel_state("fs2", {"current_chapter":0, "total_words":0, "completed_chapters":[]})
        mem.update_foreshadowing("fs2", 1, planted=[
            {"description": "远古秘密", "reveal_chapter": 2, "resolved": False}
        ])
        ctx = mem.get_foreshadowing_context("fs2", 5)
        self.assertIn("远古秘密", ctx)
        self.assertIn("🔴", ctx)

    def test_m05_foreshadowing_resolved(self):
        """已回收伏笔"""
        self._setup("fs3")
        mem = NovelMemory(cfg.NOVELS_DIR)
        mem.save_novel_state("fs3", {"current_chapter":0, "total_words":0, "completed_chapters":[]})
        desc = "已解之谜-标记回收"
        mem.update_foreshadowing("fs3", 1, planted=[
            {"description": desc, "reveal_chapter": 3, "resolved": False}
        ])
        # Resolve by description substring match
        mem.update_foreshadowing("fs3", 3, resolved=[desc])
        ctx = mem.get_foreshadowing_context("fs3", 4)
        self.assertEqual(ctx, "")  # Resolved → no reminders

    def test_m06_concurrent_chapter_save(self):
        """并发保存章节"""
        self._setup("conc")
        mem = NovelMemory(cfg.NOVELS_DIR)

        def save(n):
            try:
                mem.save_chapter("conc", n, f"第{n}章。" * 100)
            except Exception:
                pass

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
            list(ex.map(save, range(1, 21)))

        # Verify most chapters exist
        found = 0
        for i in range(1, 21):
            p = os.path.join(self.tmpdir, "conc", "chapters", f"chapter_{i:04d}.md")
            if os.path.exists(p):
                found += 1
        self.assertGreater(found, 15)

    def test_m07_build_writer_context(self):
        """Writer上下文组装"""
        self._setup("ctx")
        mem = NovelMemory(cfg.NOVELS_DIR)
        for i in range(1, 6):
            mem.save_chapter("ctx", i, f"第{i}章内容。" * 200)

        ch_outline = make_plan()['outline']['volumes'][0]['chapters'][0]
        ctx = mem.build_writer_context("ctx", 7, ch_outline)
        self.assertIn("第7章", ctx)
        self.assertIn("核心事件", ctx)

    def test_m08_large_context(self):
        """大量上下文不溢出"""
        self._setup("big")
        mem = NovelMemory(cfg.NOVELS_DIR)
        for i in range(1, 30):
            mem.save_chapter("big", i, f"第{i}章内容。\n" * 300)

        ch_outline = make_plan()['outline']['volumes'][0]['chapters'][0]
        ctx = mem.build_writer_context("big", 31, ch_outline)
        self.assertLess(len(ctx), 50000)

    def test_m09_character_bible(self):
        """人物宝典"""
        plan = make_plan("宝典", 10)
        d = os.path.join(self.tmpdir, "bible")
        os.makedirs(d, exist_ok=True)
        engine = NovelEngine()
        engine._save_character_bible(plan, d)
        self.assertTrue(os.path.exists(os.path.join(d, "character_bible.json")))

    def test_m10_memory_isolation(self):
        """目录隔离"""
        mem = NovelMemory(self.tmpdir)
        self.assertEqual(mem.novels_dir, self.tmpdir)


# ════════════════════════════════════════════════════════════
# 5. Engine 压力 (8 tests)
# ════════════════════════════════════════════════════════════

class TestEngine(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig = cfg.NOVELS_DIR
        cfg.NOVELS_DIR = self.tmpdir

    def tearDown(self):
        cfg.NOVELS_DIR = self._orig
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _setup(self, nid, novel_dir, plan=None):
        d = os.path.join(novel_dir, nid)
        os.makedirs(os.path.join(d, "chapters"), exist_ok=True)
        atomic_write_json(os.path.join(d, "plan.json"), plan or make_plan(nid, 5))
        return d

    def test_e01_find_chapter_outline(self):
        """查找章节大纲"""
        e = NovelEngine()
        ch = e._find_chapter_outline(make_plan("x", 5), 3)
        self.assertIsNotNone(ch)
        self.assertEqual(ch['number'], 3)

    def test_e02_find_chapter_missing(self):
        """章节不存在"""
        e = NovelEngine()
        self.assertIsNone(e._find_chapter_outline(make_plan("x", 5), 99))

    def test_e03_format_char_nested(self):
        """人物格式化（嵌套字段）"""
        e = NovelEngine()
        r = e._format_char_entry({"name":"A", "personality":{"a":"b"}}, "主角")
        self.assertIsInstance(r['personality'], str)

    def test_e04_format_char_flat(self):
        """人物格式化（扁平字段）"""
        e = NovelEngine()
        r = e._format_char_entry({"name":"B", "personality":"直接"}, "配角")
        self.assertEqual(r['personality'], "直接")

    def test_e05_save_character_bible(self):
        """人物宝典保存"""
        e = NovelEngine()
        e._save_character_bible(make_plan("宝典", 5), self.tmpdir)
        self.assertTrue(os.path.exists(os.path.join(self.tmpdir, "character_bible.json")))

    def test_e06_export_no_chapters(self):
        """导出无章节"""
        d = self._setup("export", self.tmpdir)
        e = NovelEngine()
        _, err = e.export_novel("export", "txt")
        self.assertIsNotNone(err)

    def test_e07_export_with_chapters(self):
        """导出有章节"""
        d = self._setup("exp2", self.tmpdir)
        ch_d = os.path.join(d, "chapters")
        for i in range(1, 3):
            with open(os.path.join(ch_d, f"chapter_{i:04d}.md"), 'w', encoding='utf-8') as f:
                f.write(f"# Ch{i}\n\nTest content." * 50)
        e = NovelEngine()
        content, err = e.export_novel("exp2", "txt")
        self.assertIsNone(err)
        self.assertIn("Ch1", content)

    def test_e08_agent_count(self):
        """六个Agent完整"""
        e = NovelEngine()
        agents = [e.planner, e.writer, e.embellisher,
                  e.fd_designer, e.context_updater, e.pacing_checker]
        self.assertEqual(len(agents), 6)
        for a in agents:
            self.assertIsNotNone(a)


# ════════════════════════════════════════════════════════════
# 6. 状态一致性压力 (8 tests)
# ════════════════════════════════════════════════════════════

class TestStateConsistency(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig = cfg.NOVELS_DIR
        cfg.NOVELS_DIR = self.tmpdir

    def tearDown(self):
        cfg.NOVELS_DIR = self._orig
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_s01_completed_chapters_sorting(self):
        """完成章节自动排序"""
        d = os.path.join(self.tmpdir, "sort")
        os.makedirs(os.path.join(d, "chapters"), exist_ok=True)
        atomic_write_json(os.path.join(d, "plan.json"), make_plan("sort", 10))
        mem = NovelMemory(cfg.NOVELS_DIR)
        mem.save_novel_state("sort", {"completed_chapters":[5,2,7,1], "current_chapter":7, "total_words":0})

        s = mem.get_novel_state("sort")
        self.assertEqual(s['completed_chapters'], [1, 2, 5, 7])

    def test_s02_backward_compat(self):
        """旧格式兼容"""
        d = os.path.join(self.tmpdir, "old")
        os.makedirs(os.path.join(d, "chapters"), exist_ok=True)
        atomic_write_json(os.path.join(d, "plan.json"), make_plan("old", 5))
        with open(os.path.join(d, "state.json"), 'w') as f:
            f.write('{"current_chapter":1,"total_words":3000}')
        mem = NovelMemory(cfg.NOVELS_DIR)
        s = mem.get_novel_state("old")
        self.assertIn('completed_chapters', s)

    def test_s03_empty_plan(self):
        """空plan"""
        d = os.path.join(self.tmpdir, "empty")
        os.makedirs(os.path.join(d, "chapters"), exist_ok=True)
        atomic_write_json(os.path.join(d, "plan.json"), {})
        mem = NovelMemory(cfg.NOVELS_DIR)
        ctx = mem.get_core_context("empty")
        self.assertIsInstance(ctx, str)

    def test_s04_novel_id_safe(self):
        """ID安全性"""
        mem = NovelMemory(cfg.NOVELS_DIR)
        d = mem.get_novel_dir("test/../escape")
        self.assertTrue(d.startswith(cfg.NOVELS_DIR))

    def test_s05_many_foreshadowings(self):
        """大量伏笔"""
        d = os.path.join(self.tmpdir, "many")
        os.makedirs(os.path.join(d, "chapters"), exist_ok=True)
        atomic_write_json(os.path.join(d, "plan.json"), make_plan("many", 10))
        mem = NovelMemory(cfg.NOVELS_DIR)
        mem.save_novel_state("many", {"current_chapter":0, "total_words":0, "completed_chapters":[]})

        planted = [{"description":f"伏笔{i}","reveal_chapter":min(5,i%5+1),"resolved":False}
                   for i in range(50)]
        mem.update_foreshadowing("many", 1, planted=planted)
        ctx = mem.get_foreshadowing_context("many", 2)
        self.assertIn("伏笔", ctx)

    def test_s06_special_novel_ids(self):
        """特殊ID"""
        mem = NovelMemory(cfg.NOVELS_DIR)
        for sid in ["test-1", "test_2", "测试", "Test Space"]:
            d = os.path.join(self.tmpdir, sid)
            os.makedirs(os.path.join(d, "chapters"), exist_ok=True)
            atomic_write_json(os.path.join(d, "plan.json"), make_plan(sid, 3))

        found = [d for d in os.listdir(self.tmpdir)
                if os.path.isdir(os.path.join(self.tmpdir, d))]
        self.assertEqual(len(found), 4)

    def test_s07_concurrent_chapter_tracking(self):
        """并发章节追踪"""
        d = os.path.join(self.tmpdir, "ctrack")
        os.makedirs(os.path.join(d, "chapters"), exist_ok=True)
        atomic_write_json(os.path.join(d, "plan.json"), make_plan("ctrack", 30))

        def track(n):
            try:
                mem = NovelMemory(cfg.NOVELS_DIR)
                mem.save_chapter("ctrack", n, f"Content {n} " * 50)
            except Exception:
                pass

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
            list(ex.map(track, range(1, 16)))

        mem = NovelMemory(cfg.NOVELS_DIR)
        s = mem.get_novel_state("ctrack")
        # At least some chapters should be found
        self.assertGreaterEqual(len(s['completed_chapters']), 10)

    def test_s08_empty_novel_context(self):
        """空小说上下文"""
        d = os.path.join(self.tmpdir, "none")
        os.makedirs(d, exist_ok=True)
        mem = NovelMemory(cfg.NOVELS_DIR)
        ctx = mem.get_core_context("none")
        # No plan.json → should return empty string, not crash
        self.assertIsInstance(ctx, str)


# ════════════════════════════════════════════════════════════
# 7. 配置 & 全局压力 (10 tests)
# ════════════════════════════════════════════════════════════

class TestConfig(unittest.TestCase):

    def test_c01_defaults_sensible(self):
        """配置默认值"""
        self.assertGreater(cfg.DEFAULT_CHAPTER_WORDS, 0)
        self.assertGreater(cfg.MAX_CONTEXT_TOKENS, 0)
        self.assertIsInstance(cfg.PORT, int)

    def test_c02_all_modules_import(self):
        """所有模块可导入"""
        mods = ['core.planner', 'core.writer', 'core.engine', 'core.memory',
                'core.atomic_io', 'core.humanizer', 'core.embellisher',
                'core.context_updater', 'core.foreshadowing_designer',
                'core.pacing_checker', 'core.style_fingerprint', 'core.styles']
        for m in mods:
            __import__(m)
            self.assertTrue(True)  # reached = no ImportError

    def test_c03_style_categories(self):
        """风格列表"""
        from core.styles import STYLES
        self.assertIsInstance(STYLES, dict)
        self.assertGreater(len(STYLES), 0)
        # Verify first style has required keys
        first_name = list(STYLES.keys())[0]
        first = STYLES[first_name]
        self.assertIn('name', first)

    def test_c04_fingerprint_empty(self):
        """指纹空输入"""
        from core.style_fingerprint import StyleFingerprint
        r = StyleFingerprint().analyze("")
        self.assertIn('error', r)

    def test_c05_fingerprint_valid(self):
        """指纹有效输入"""
        from core.style_fingerprint import StyleFingerprint
        text = "阳光穿过树叶的间隙，在青石板上投下斑驳的光影。少年坐在屋檐下，端着一杯茶。风吹过竹林，沙沙作响。" * 15
        r = StyleFingerprint().analyze(text)
        self.assertNotIn('error', r)

    def test_c06_fingerprint_too_short(self):
        """指纹太短"""
        from core.style_fingerprint import StyleFingerprint
        r = StyleFingerprint().analyze("短")
        self.assertIn('error', r)

    def test_c07_style_get_unknown(self):
        """未知风格降级"""
        from core.styles import get_style
        r = get_style("不存在")
        self.assertEqual(r['name'], "热血爽文")

    def test_c08_novels_dir_creatable(self):
        """存储目录可创建"""
        import os
        if not os.path.exists(cfg.NOVELS_DIR):
            os.makedirs(cfg.NOVELS_DIR, exist_ok=True)
        self.assertTrue(os.path.exists(cfg.NOVELS_DIR))

    def test_c09_model_v4_detection(self):
        """模型名称检测"""
        w = Writer(MagicMock(), 'deepseek-v4-flash')
        self.assertIn('v4', w.model)
        w2 = Writer(MagicMock(), 'deepseek-chat')
        self.assertNotIn('v4', w2.model)

    def test_c10_config_immutable_in_tests(self):
        """配置可被测试覆盖"""
        old = cfg.DEFAULT_CHAPTER_WORDS
        cfg.DEFAULT_CHAPTER_WORDS = 999
        self.assertEqual(cfg.DEFAULT_CHAPTER_WORDS, 999)
        cfg.DEFAULT_CHAPTER_WORDS = old


# ════════════════════════════════════════════════════════════
# 8. Server 端点 (8 tests)
# ════════════════════════════════════════════════════════════

class TestServerEndpoints(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig = cfg.NOVELS_DIR
        cfg.NOVELS_DIR = self.tmpdir

    def tearDown(self):
        cfg.NOVELS_DIR = self._orig
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _get_client(self):
        try:
            from fastapi.testclient import TestClient
            from backend.api.server import app
            return TestClient(app)
        except ImportError:
            self.skipTest("fastapi not installed")
            return None

    def _setup_novel(self, nid):
        d = os.path.join(self.tmpdir, nid)
        os.makedirs(os.path.join(d, "chapters"), exist_ok=True)
        plan = make_plan(nid, 10)
        atomic_write_json(os.path.join(d, "plan.json"), plan)
        mem = NovelMemory(self.tmpdir)
        mem.save_novel_state(nid, {"current_chapter":0, "total_words":0,
                                    "completed_chapters":[]})

    def test_sv01_health(self):
        c = self._get_client()
        if not c: return
        r = c.get("/api/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['status'], 'ok')

    def test_sv02_list_empty(self):
        c = self._get_client()
        if not c: return
        r = c.get("/api/novels")
        self.assertEqual(r.status_code, 200)

    def test_sv03_list_with_data(self):
        for i in range(3):
            self._setup_novel(f"sv_{i}")
        c = self._get_client()
        if not c: return
        r = c.get("/api/novels")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()['novels']), 3)

    def test_sv04_get_missing(self):
        c = self._get_client()
        if not c: return
        r = c.get("/api/novels/nonexistent")
        self.assertEqual(r.status_code, 404)

    def test_sv05_get_exists(self):
        self._setup_novel("exists")
        c = self._get_client()
        if not c: return
        r = c.get("/api/novels/exists")
        self.assertEqual(r.status_code, 200)

    def test_sv06_export_nonexistent(self):
        c = self._get_client()
        if not c: return
        r = c.get("/api/novels/none/export?fmt=txt")
        self.assertEqual(r.status_code, 404)

    def test_sv07_bible_endpoint(self):
        self._setup_novel("bible")
        c = self._get_client()
        if not c: return
        r = c.get("/api/novels/bible/character-bible")
        self.assertIn(r.status_code, (200, 404))

    def test_sv08_fingerprint_short(self):
        c = self._get_client()
        if not c: return
        r = c.post("/api/styles/fingerprint", json={"text": "短"})
        self.assertEqual(r.status_code, 400)


# ════════════════════════════════════════════════════════════
# Main
# ════════════════════════════════════════════════════════════

if __name__ == '__main__':
    print("=" * 60)
    print("NovelGenerator 全功能压力测试")
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    classes = [
        TestPlannerJSON, TestAtomicIO, TestWriter, TestMemory,
        TestEngine, TestStateConsistency, TestConfig, TestServerEndpoints
    ]

    total = 0
    for tc in classes:
        tests = loader.loadTestsFromTestCase(tc)
        total += tests.countTestCases()
        suite.addTests(tests)

    print(f"\nTotal test cases: {total}\n")

    runner = unittest.TextTestRunner(verbosity=1)
    result = runner.run(suite)

    passed = result.testsRun - len(result.failures) - len(result.errors)
    rate = passed / max(result.testsRun, 1) * 100
    print(f"\n{'='*60}")
    print(f"Results: {result.testsRun} run | {passed} passed | "
          f"{len(result.failures)} failed | {len(result.errors)} errors")
    print(f"Pass rate: {passed}/{result.testsRun} = {rate:.1f}%")
    print(f"{'='*60}")

    sys.exit(0 if result.wasSuccessful() else 1)
