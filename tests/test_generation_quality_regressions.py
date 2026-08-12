import asyncio
from types import SimpleNamespace

from backend.core.character_state import CharacterStateTracker
from backend.core.outline_compliance import OutlineComplianceChecker
from backend.core.shared_memory import SharedMemoryManager
from backend.core.writer import Writer


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


def test_consistency_parser_repairs_trailing_commas():
    from backend.core.consistency_validator import _parse_json_response

    parsed = _parse_json_response('{"violations": [{"severity": "P0",}],}')

    assert parsed["violations"][0]["severity"] == "P0"
