import copy

from backend.core.chapter_contract import (
    CHAPTER_CONTRACT_VERSION,
    migrate_plan_chapter_contracts,
    normalize_chapter_contract,
)
from backend.core.mixins.generation import GenerationMixin


def _legacy_chapter():
    return {
        "number": 2,
        "title": "密室来信",
        "summary": "林岚拆开封蜡信，发现父亲失踪前留下的坐标。",
        "cause_from_prev": "林岚从档案馆带回一封未拆的信。",
        "opening_scene": "深夜，林岚独自在厨房检查封蜡。",
        "bridge_to_next": "林岚决定天亮前去旧码头。",
        "hook": "窗外有人拍下她展开坐标的照片。",
        "scene_beats": [
            "林岚确认封蜡没有被重新粘过",
            {"beat": 2, "name": "发现", "key_action": "她从夹层抽出父亲手写的码头坐标"},
            {"beat": 3, "name": "阻力", "key_action": "整栋楼突然断电，门外传来脚步声"},
        ],
        "requirement_ids": ["R_EVENT"],
    }


def test_legacy_chapter_is_normalized_without_discarding_old_fields():
    legacy = _legacy_chapter()

    contract = normalize_chapter_contract(legacy)

    assert contract["chapter_contract_version"] == CHAPTER_CONTRACT_VERSION
    assert contract["summary"] == legacy["summary"]
    assert contract["before_state"] == [legacy["cause_from_prev"], legacy["opening_scene"]]
    assert contract["must_happen"] == [
        "林岚确认封蜡没有被重新粘过",
        "她从夹层抽出父亲手写的码头坐标",
        "整栋楼突然断电，门外传来脚步声",
    ]
    assert contract["after_state"] == [legacy["bridge_to_next"]]
    assert contract["new_information"] == []
    assert contract["open_loop"] == [legacy["hook"]]
    assert contract["forbidden_repeats"] == []
    assert contract["requirement_ids"] == ["R_EVENT"]
    assert legacy.get("chapter_contract_version") is None


def test_plan_migration_is_idempotent_and_reports_contract_readiness():
    plan = {
        "outline": {"volumes": [{"chapters": [_legacy_chapter()]}]},
        "_meta": {},
    }

    first = migrate_plan_chapter_contracts(plan)
    snapshot = copy.deepcopy(plan)
    second = migrate_plan_chapter_contracts(plan)

    assert first["migrated_count"] == 1
    assert first["ready_count"] == 1
    assert second["migrated_count"] == 0
    assert plan == snapshot
    assert plan["_meta"]["chapter_contract"]["schema_version"] == CHAPTER_CONTRACT_VERSION


def test_migration_tolerates_malformed_legacy_versions_and_beat_numbers():
    chapter = {
        "chapter_contract_version": "legacy",
        "summary": "主角找到证据并决定报警。",
        "scene_beats": [{"beat": "first", "key_action": "主角在抽屉夹层找到转账记录"}],
    }
    plan = {
        "outline": {"volumes": [{"chapters": [chapter]}]},
        "_meta": {"chapter_contract": {"schema_version": "legacy"}},
    }

    report = migrate_plan_chapter_contracts(plan)

    assert report["migrated_count"] == 1
    assert chapter["scene_beats"][0]["beat"] == 1


class _OutlineProbe(GenerationMixin):
    pass


def test_hard_outline_prompt_uses_the_full_chapter_contract():
    chapter = normalize_chapter_contract({
        **_legacy_chapter(),
        "new_information": ["坐标指向已经废弃的七码头"],
        "forbidden_repeats": ["不要再次用断电结束场景"],
    })

    text = _OutlineProbe()._hard_outline_text(chapter)

    assert "章前状态" in text
    assert "必须发生" in text
    assert "章后状态" in text
    assert "新增信息" in text
    assert "未闭环" in text
    assert "禁止重复" in text
    assert "七码头" in text


def test_workbench_exposes_the_current_chapter_contract():
    from pathlib import Path

    html = (Path(__file__).parents[1] / "web" / "index.html").read_text(encoding="utf-8")

    assert "章节执行契约" in html
    assert "currentOutline.must_happen" in html
    assert "currentOutline.forbidden_repeats" in html
