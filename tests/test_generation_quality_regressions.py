import asyncio
import json
from types import SimpleNamespace

from backend.core.character_state import CharacterStateTracker
from backend.core.outline_compliance import OutlineComplianceChecker
from backend.core.shared_memory import SharedMemoryManager
from backend.core.writer import Writer
from backend.core.beat_decomposer import BeatDecomposer
from backend.core.atomic_writer import _beat_contract_score
from backend.core.planner import Planner


class _FakeMemory:
    def __init__(self):
        self.writes = []

    def read(self, memory_type, novel_id):
        return {}

    def write(self, memory_type, novel_id, data):
        self.writes.append((memory_type, novel_id, data))


class _FakeClient:
    def __init__(self):
        def create(**kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))]
            )

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def test_writer_stream_helper_runs_inside_asyncio_loop():
    writer = Writer(None, "mock")
    chunk = SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content="片段"))]
    )
    writer._create = lambda **kwargs: iter([chunk])

    result = asyncio.run(writer._create_stream_chunks(stream=True))

    assert result == ["片段"]


def test_beat_decomposer_maps_scene_contract_into_writer_beats():
    outline = {
        "summary": "沈砚调查被替换的封条并赴旧码头",
        "hook": "旧码头有人等他",
        "characters": ["沈砚", "周岚"],
        "scene_beats": [
            {
                "name": "发现封条",
                "key_action": "沈砚撬开档案柜，发现封条编号被替换",
                "entry_hook": "门外传来保安的脚步声",
                "reject_outcome": "他若交出钥匙，真正的封条会被销毁",
                "state_output": {"flags": ["发现假封条"], "relations": {}},
            },
            {
                "name": "赴约",
                "key_action": "沈砚带着证据前往旧码头",
                "entry_hook": "陌生账号发来倒计时",
                "reject_outcome": "拒绝赴约就会失去唯一证人",
                "state_output": {"flags": ["旧码头赴约"], "relations": {}},
            },
        ],
    }

    beats = BeatDecomposer(seed=7).decompose(outline, 2, available_characters=["沈砚", "周岚"])
    payload = [beat.to_dict() for beat in beats]

    assert any("撬开档案柜" in beat["key_event"] for beat in payload)
    assert any("旧码头" in beat["key_event"] for beat in payload)
    assert payload[0]["state_change"]["flags"] == ["发现假封条"]
    assert payload[-1]["reader_question"]
    assert all("本章 " not in beat["key_event"] for beat in payload)


def test_beat_prompt_exposes_choice_and_state_change_contract():
    outline = {
        "summary": "调查失踪案",
        "scene_beats": [{
            "key_action": "找到被撕碎的名单",
            "reject_outcome": "不追查就会错过凶手",
            "state_output": {"flags": ["拿到名单"], "relations": {}},
        }],
    }

    beat = BeatDecomposer(seed=1).decompose(outline, 1)[0]
    prompt = beat.to_prompt()

    assert "选择/代价" in prompt
    assert "状态变化" in prompt
    assert "不追查就会错过凶手" in prompt


def test_atomic_candidate_score_prefers_event_progress_over_generic_prose():
    outline = {
        "summary": "调查失踪案",
        "scene_beats": [{
            "key_action": "找到被撕碎的名单",
            "reject_outcome": "不追查就会错过凶手",
            "state_output": {"flags": ["拿到名单"], "relations": {}},
        }],
    }
    beat = BeatDecomposer(seed=1).decompose(outline, 1)[0]

    generic = "雨水沿着屋檐落下，他想起了很多年前的那个夜晚，心里泛起说不清的情绪。"
    progressing = "沈砚撕开档案袋，在碎纸堆里找到被撕碎的名单，并把证据收进内袋。"

    assert _beat_contract_score(progressing, beat) > _beat_contract_score(generic, beat)


def test_atomic_closing_candidate_penalizes_truncated_last_sentence():
    outline = {
        "summary": "调查失踪案",
        "hook": "名单背面出现新的名字",
        "scene_beats": [{
            "key_action": "找到被撕碎的名单",
            "reader_question": "名单背面是谁留下的名字",
        }],
    }
    beats = BeatDecomposer(seed=1).decompose(outline, 1, is_first_chapter=True)
    closing = beats[-1]

    truncated = "他翻到名单最后一页，看见一个熟悉的名字，手指停在那一行"
    complete = "他翻到名单最后一页，看见一个熟悉的名字。名单背面，另一个名字正在渗出来。"

    assert _beat_contract_score(complete, closing) > _beat_contract_score(truncated, closing)


def test_atomic_closing_prompt_requires_a_concrete_open_loop():
    outline = {
        "summary": "调查失踪案",
        "hook": "名单背面出现新的名字",
        "scene_beats": [{
            "key_action": "找到被撕碎的名单",
            "reader_question": "名单背面是谁留下的名字",
        }],
    }
    closing = BeatDecomposer(seed=1).decompose(
        outline, 1, is_first_chapter=True
    )[-1]
    from backend.core.atomic_writer import AtomicWriter

    prompt = AtomicWriter(None, "mock")._build_beat_prompt(
        closing, "", "", chapter_context=""
    )

    assert "具体未决向量" in prompt
    assert "下一章要追查" in prompt


def test_atomic_prompt_requires_an_observable_payoff_per_beat():
    outline = {
        "summary": "调查失踪案",
        "scene_beats": [{"key_action": "找到被撕碎的名单"}],
    }
    beat = BeatDecomposer(seed=1).decompose(outline, 1)[0]
    from backend.core.atomic_writer import AtomicWriter

    prompt = AtomicWriter(None, "mock")._build_beat_prompt(
        beat, "", "", chapter_context=""
    )

    assert "可验证的即时回报" in prompt
    assert "证据、决定、关系变化或代价" in prompt


def test_atomic_opening_candidate_requires_action_near_the_start():
    outline = {
        "summary": "调查失踪案",
        "scene_beats": [{"key_action": "撬开档案柜找到名单"}],
    }
    opening = BeatDecomposer(seed=1).decompose(outline, 1, is_first_chapter=True)[0]

    delayed = ("雨声铺满了走廊，旧灯在头顶闪烁了很久。" + "走廊尽头的灰尘被风卷起来，贴在墙面上，空气里只有潮湿木头和旧纸张的气味。") * 3 + "直到最后一段，他才撬开档案柜找到名单。"
    immediate = "沈砚撬开档案柜，铁屑落在手背上，里面那份名单少了一页。"

    assert _beat_contract_score(immediate, opening) > _beat_contract_score(delayed, opening)


def test_atomic_opening_prompt_requires_action_and_irregularity_early():
    outline = {
        "summary": "调查失踪案",
        "scene_beats": [{"key_action": "撬开档案柜找到名单"}],
    }
    opening = BeatDecomposer(seed=1).decompose(
        outline, 1, is_first_chapter=True
    )[0]
    from backend.core.atomic_writer import AtomicWriter

    prompt = AtomicWriter(None, "mock")._build_beat_prompt(
        opening, "", "", chapter_context=""
    )

    assert "前80字" in prompt
    assert "具体行动" in prompt
    assert "异常或阻力" in prompt


def test_planner_repair_builds_non_placeholder_scene_contract():
    outline = {"volumes": [{"chapters": [{
        "number": 1,
        "summary": "沈砚在档案室发现封条编号被替换",
        "conflict": "保安回来阻止他取证",
        "hook": "名单背面出现父亲写下的名字",
        "bridge_to_next": "沈砚必须在天亮前赴旧码头",
    }]}]}

    repaired = Planner.repair_outline(outline)
    beats = repaired["volumes"][0]["chapters"][0]["scene_beats"]

    assert len(beats) == 5
    assert all(beat["key_action"] for beat in beats)
    assert all("具体发生了什么" not in beat["key_action"] for beat in beats)
    assert beats[0]["key_action"] != beats[-1]["key_action"]
    assert beats[-1]["key_action"] == "名单背面出现父亲写下的名字"


def test_planner_repair_replaces_llm_placeholder_key_actions():
    outline = {"volumes": [{"chapters": [{
        "number": 1,
        "summary": "主角拿到失踪案名单",
        "hook": "名单上出现熟人的名字",
        "scene_beats": [
            {"beat": 1, "key_action": "具体发生了什么——必填本章第1关键节点的具体事件"},
            {"beat": 2, "key_action": "核心冲突展开"},
            {"beat": 5, "key_action": "留下悬念"},
        ],
    }]}]}

    repaired = Planner.repair_outline(outline)
    beats = repaired["volumes"][0]["chapters"][0]["scene_beats"]

    assert beats[0]["key_action"] != "具体发生了什么——必填本章第1关键节点的具体事件"
    assert beats[1]["key_action"] != "核心冲突展开"
    assert beats[2]["key_action"] != "留下悬念"


def test_character_state_update_persists_empty_extraction_without_asyncio_name_error():
    memory = _FakeMemory()
    tracker = CharacterStateTracker(_FakeClient(), "mock", memory)

    asyncio.run(tracker.update_from_chapter("novel", 1, "沈砚走进档案室。"))

    assert memory.writes
    assert memory.writes[-1][0] == "global_state"


def test_writer_context_accepts_string_scene_beats(tmp_path):
    memory = SharedMemoryManager(str(tmp_path))
    novel_id = "context_probe"
    memory.create_novel_workspace(novel_id)
    memory.write(
        "plan",
        novel_id,
        {
            "title": novel_id,
            "worldbuilding": {"era": "当代", "power_system": "现实"},
            "characters": {
                "protagonist": {"name": "沈砚", "identity": "档案修复师"},
                "supporting": [],
            },
            "outline": {"total_chapters": 1, "volumes": []},
        },
    )

    context = memory.build_writer_context(
        novel_id,
        1,
        {"title": "探针", "scene_beats": ["发现被替换的封条", "找到借阅记录"]},
    )

    assert "发现被替换的封条" in context
    assert "找到借阅记录" in context


def test_writer_context_includes_character_gender_anchor(tmp_path):
    memory = SharedMemoryManager(str(tmp_path))
    novel_id = "gender_probe"
    memory.create_novel_workspace(novel_id)
    memory.write(
        "plan",
        novel_id,
        {
            "title": novel_id,
            "worldbuilding": {"era": "当代", "power_system": "现实"},
            "characters": {
                "protagonist": {
                    "name": "沈砚",
                    "identity": "档案修复师",
                    "gender": "男性",
                },
                "supporting": [],
            },
            "outline": {"total_chapters": 1, "volumes": []},
        },
    )

    context = memory.build_writer_context(
        novel_id, 1, {"title": "探针", "characters": ["沈砚"]}
    )

    assert "性别/代词: 男性" in context


def test_shared_memory_exposes_invalidate_alias(tmp_path):
    memory = SharedMemoryManager(str(tmp_path))
    novel_id = "invalidate_probe"
    memory.create_novel_workspace(novel_id)
    memory.write("state", novel_id, {"current_chapter": 0})
    memory.read("state", novel_id)

    memory.invalidate("state", novel_id)

    assert (novel_id, "state") not in memory._cache


def test_outline_compliance_checks_string_scene_beats():
    checker = OutlineComplianceChecker()

    items = checker.extract_check_items(
        {"summary": "推进调查", "scene_beats": ["发现校验码", "锁死库房"]}
    )

    assert [item["id"] for item in items if item["id"].startswith("beat")] == [
        "beat1",
        "beat2",
    ]
    assert items[1]["needle"] == "发现校验码"


def test_generation_normalizer_works_when_loaded_as_backend_package():
    from backend.core.mixins.generation import _normalize_chapter_text

    normalized = _normalize_chapter_text("短句一\n\n短句二\n\n这是一个足够长的段落，用于验证段落规范化函数能够在 backend 包导入方式下正常工作。")

    assert "短句一" in normalized
    assert "短句二" in normalized


def test_final_quality_merges_post_consistency_violations():
    from backend.core.mixins.generation import _merge_quality_with_consistency

    merged = _merge_quality_with_consistency(
        {"score": 100, "issues": []},
        {
            "score": 40,
            "violations": [
                {"severity": "P0", "description": "时间线前后矛盾"},
            ],
        },
    )

    assert merged["score"] == 40
    assert merged["issues"] == ["[P0] 时间线前后矛盾"]


def test_final_quality_downgrades_when_consistency_result_has_parse_error():
    from backend.core.mixins.generation import _merge_quality_with_consistency

    merged = _merge_quality_with_consistency(
        {"score": 100, "issues": []},
        {"score": 100, "violations": [], "error": "invalid JSON"},
    )

    assert merged["score"] == 0
    assert merged["issues"] == ["[P1] 一致性校验未完成: invalid JSON"]


def test_final_quality_includes_narrative_structure_without_hiding_pacing_issues():
    from backend.core.mixins.generation import _merge_quality_with_narrative

    merged = _merge_quality_with_narrative(
        {"score": 90, "issues": []},
        {
            "hook": {"strength": 2},
            "structure": {
                "score": 30,
                "metrics": {"opening": 20},
                "issues": ["开篇缺少具体行动或立即冲突"],
            },
        },
    )

    assert merged["score"] < 90
    assert merged["narrative"]["structure"]["score"] == 30
    assert "[叙事结构] 开篇缺少具体行动或立即冲突" in merged["issues"]


def test_consistency_parser_repairs_trailing_commas():
    from backend.core.consistency_validator import _parse_json_response

    parsed = _parse_json_response('{"violations": [{"severity": "P0",}],}')

    assert parsed["violations"][0]["severity"] == "P0"


def test_writer_context_includes_character_age_anchor(tmp_path):
    memory = SharedMemoryManager(str(tmp_path))
    novel_id = "age_probe"
    memory.create_novel_workspace(novel_id)
    memory.write(
        "plan",
        novel_id,
        {
            "title": novel_id,
            "worldbuilding": {"era": "contemporary", "power_system": "realistic"},
            "characters": {
                "protagonist": {
                    "name": "Alex",
                    "identity": "archivist",
                    "age": "29",
                    "gender": "male",
                },
                "supporting": [],
            },
            "outline": {"total_chapters": 1, "volumes": []},
        },
    )

    context = memory.build_writer_context(
        novel_id, 1, {"title": "Archive", "characters": ["Alex"]}
    )

    assert "29" in context
    assert "设备工作必须先交代电源" in context


def test_l2_context_includes_hard_character_anchors_and_chapter_outline():
    from backend.core.consistency_validator import ConsistencyValidator

    captured = {}

    class _FakeResilient:
        def create(self, **kwargs):
            captured["prompt"] = kwargs["messages"][1]["content"]
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))]
            )

    validator = ConsistencyValidator()
    validator.client = object()
    validator.model = "mock"
    validator._resilient = _FakeResilient()

    validator._l2_validate(
        "Alex entered the archive basement.",
        1,
        {
            "worldbuilding": {"era": "contemporary", "power_system": "realistic"},
            "characters": {
                "protagonist": {
                    "name": "Alex",
                    "identity": "archivist",
                    "age": "29",
                    "gender": "male",
                },
                "supporting": [],
            },
        },
        prev_chapters=None,
        global_state=None,
        chapter_outline={
            "title": "The Basement",
            "summary": "Find the missing seal",
            "scene_beats": ["Enter archive basement", "Discover printer clue"],
        },
    )

    prompt = captured["prompt"]
    assert "Alex" in prompt
    assert "29" in prompt
    assert "male" in prompt
    assert "Find the missing seal" in prompt
    assert "Discover printer clue" in prompt


def test_storygraph_parser_accepts_fenced_json_with_trailing_comma():
    from backend.core.storygraph import _parse_storygraph_response

    parsed = _parse_storygraph_response(
        '```json\n{"thread_updates": [], "char_updates": [],}\n```\n'
    )

    assert parsed["thread_updates"] == []


def test_storygraph_keeps_non_thread_constraints_and_deduplicates_causal_links():
    from backend.core.storygraph import StoryGraph

    graph = StoryGraph()
    graph.add_foreshadow("fs-1", "钟声里的第二个回响", 1, 4, importance=4)
    graph.ensure_char("林晚")
    graph.add_causal_link(1, "拿到旧钥匙", 4, "打开地下门")
    graph.add_causal_link(1, "拿到旧钥匙", 4, "打开地下门")

    assert len(graph.data["causal_links"]) == 1
    assert "未闭环因果链" in graph.get_causal_context(4)


def test_shared_memory_storygraph_context_survives_without_plot_threads(tmp_path):
    memory = SharedMemoryManager(str(tmp_path))
    novel_id = "graph-only"
    novel_dir = tmp_path / novel_id
    novel_dir.mkdir()
    (novel_dir / "storygraph.json").write_text(json.dumps({
        "plot_threads": {},
        "foreshadow_ledger": {
            "fs-1": {
                "description": "钟声里的第二个回响",
                "planted_chapter": 1,
                "planned_payoff_chapter": 3,
                "status": "planted",
                "importance": 4,
            }
        },
        "char_snapshots": {},
        "causal_links": [],
    }, ensure_ascii=False), encoding="utf-8")

    context = memory._build_storygraph_context(novel_id, 3, {"characters": []})

    assert "即将到期的伏笔" in context
    assert "钟声里的第二个回响" in context


def test_storygraph_intervention_uses_progress_and_current_outline(tmp_path):
    from backend.core.storygraph_interventions import analyze_and_inject

    novel_dir = tmp_path / "intervention"
    chapters_dir = novel_dir / "chapters"
    chapters_dir.mkdir(parents=True)
    for chapter in range(1, 5):
        (chapters_dir / f"chapter_{chapter:04d}.md").write_text(
            f"第{chapter}章", encoding="utf-8"
        )
    (novel_dir / "global_state.json").write_text(json.dumps({
        "chapters_summary": {str(ch): f"摘要{ch}" for ch in range(1, 5)}
    }), encoding="utf-8")
    (novel_dir / "storygraph.json").write_text(json.dumps({
        "plot_threads": {},
        "foreshadow_ledger": {},
        "char_snapshots": {
            "林晚": {
                "last_chapter_appeared": 1,
                "active_goals": ["找出内鬼"],
                "current_emotion": "焦虑",
            }
        },
        "causal_links": [
            {"cause_chapter": i, "cause_event": f"起因{i}",
             "effect_chapter": 5, "effect_event": f"结果{i}",
             "status": "active"}
            for i in range(1, 6)
        ],
    }), encoding="utf-8")

    context = analyze_and_inject(
        str(novel_dir), 5, {"characters": ["林晚"]}
    )

    assert "DOPAMINE_CHECK" in context
    assert "TENSION_ADJUST" in context
    assert "CHARACTER_ABSENT" not in context


def test_story_director_llm_degrades_cleanly_for_legacy_object():
    from backend.core.interactive.story_director import StoryDirector

    director = StoryDirector.__new__(StoryDirector)

    assert director._llm("system", "user") is None


def test_interactive_prompt_can_read_shared_storygraph(tmp_path):
    from backend.core.interactive.story_director import StoryDirector

    novel_dir = tmp_path / "interactive-graph"
    novel_dir.mkdir()
    (novel_dir / "storygraph.json").write_text(json.dumps({
        "plot_threads": {
            "main": {
                "name": "旧钥匙主线", "status": "active", "priority": 5,
                "current_tension": 7, "key_nodes": [],
                "type": "main_plot", "next_planned": "找到地下门",
            }
        },
        "foreshadow_ledger": {},
        "char_snapshots": {},
        "causal_links": [],
    }, ensure_ascii=False), encoding="utf-8")

    director = StoryDirector.__new__(StoryDirector)
    director.store = SimpleNamespace(novels_dir=str(tmp_path))
    director.engine = None
    state = {"novel_id": "interactive-graph", "outline_progress": {"idx": 0},
             "casts": {}}

    context = director._storygraph_context("interactive-graph", state)

    assert "旧钥匙主线" in context
    assert "找到地下门" in context


def test_consistency_repair_rank_does_not_prefer_a_later_worse_draft():
    from backend.core.mixins.generation import _consistency_candidate_rank

    better = {
        "score": 25,
        "violations": [
            {"severity": "P0"},
            {"severity": "P1"},
        ],
    }
    worse = {
        "score": 10,
        "violations": [
            {"severity": "P0"},
            {"severity": "P0"},
        ],
    }

    assert _consistency_candidate_rank(better) < _consistency_candidate_rank(worse)
