from backend.core.requirements_contract import (
    active_requirements,
    assign_requirements_to_outline,
    evaluate_requirements_against_text,
    normalize_requirements,
)
from backend.core.mixins.generation import GenerationMixin
from backend.core.mixins.requirements import RequirementsMixin


def _requirements():
    return {
        "subtasks": [
            {
                "id": "R001",
                "title": "Reveal the hidden ledger",
                "description": "The protagonist must discover the ledger in chapter one.",
                "priority": "P0",
                "must_include": ["hidden ledger", "chapter one"],
                "must_avoid": ["magic reset"],
                "generation_hint": "Use a concrete clue and a visible consequence.",
                "quality_criteria": ["The clue changes the protagonist's next action."],
                "scope": "opening",
                "chapter_numbers": [1],
            }
        ]
    }


def _typed_requirements():
    return {
        "schema_version": 2,
        "version": 4,
        "updated_at": "2026-08-20T00:00:00+00:00",
        "subtasks": [
            {
                "id": "R_PLAN",
                "kind": "planning_only",
                "title": "Plan three volumes",
                "priority": "P0",
                "scope": "global",
                "must_include": ["three volumes"],
            },
            {
                "id": "R_RULE",
                "kind": "book_invariant",
                "title": "Keep first-person narration",
                "priority": "P0",
                "scope": "global",
                "must_include": ["first-person narration"],
                "must_avoid": ["head-hopping"],
            },
            {
                "id": "R_PREF",
                "kind": "preference",
                "title": "Restrained prose",
                "priority": "P1",
                "scope": "global",
                "quality_criteria": ["Dialogue stays concise."],
            },
            {
                "id": "R_EVENT",
                "kind": "chapter_event",
                "title": "Open the letter",
                "priority": "P0",
                "scope": "chapter",
                "chapter_numbers": [2],
                "must_include": ["sealed letter"],
            },
            {
                "id": "R_MILE",
                "kind": "milestone",
                "title": "Reveal the betrayal",
                "priority": "P0",
                "scope": "ending",
                "must_include": ["betrayal"],
            },
        ],
    }


def _four_chapter_plan():
    return {
        "title": "Typed Contract",
        "outline": {
            "total_chapters": 4,
            "volumes": [
                {
                    "chapters": [
                        {"chapter_num": 1, "title": "One"},
                        {"chapter_num": 2, "title": "Two"},
                        {"chapter_num": 3, "title": "Three"},
                        {"chapter_num": 4, "title": "Four"},
                    ]
                }
            ],
        },
    }


def test_legacy_requirements_are_migrated_to_stable_v2_kinds():
    legacy = {
        "schema_version": 1,
        "subtasks": [
            {"id": "R1", "category": "structure", "sub_category": "volume_plan", "title": "Plan volumes"},
            {"id": "R2", "category": "style", "sub_category": "dialogue", "title": "Natural dialogue"},
            {"id": "R3", "category": "plot", "sub_category": "turning_points", "title": "Three turns"},
            {"id": "R4", "scope": "chapter", "chapter_numbers": [2], "title": "Open a letter"},
            {"id": "R5", "category": "worldbuilding", "sub_category": "rules", "title": "No time travel"},
        ],
    }

    migrated = normalize_requirements(legacy)

    assert migrated["schema_version"] == 2
    assert [item["kind"] for item in migrated["subtasks"]] == [
        "planning_only",
        "preference",
        "milestone",
        "chapter_event",
        "book_invariant",
    ]
    assert [item["id"] for item in migrated["subtasks"]] == ["R1", "R2", "R3", "R4", "R5"]


def test_assignment_only_puts_chapter_owned_requirements_on_outline():
    plan = _four_chapter_plan()

    result = assign_requirements_to_outline(_typed_requirements(), plan)

    items = {item["id"]: item for item in result["subtasks"]}
    assert items["R_PLAN"]["chapter_numbers"] == []
    assert items["R_RULE"]["chapter_numbers"] == []
    assert items["R_PREF"]["chapter_numbers"] == []
    assert items["R_EVENT"]["chapter_numbers"] == [2]
    assert items["R_MILE"]["chapter_numbers"] == [4]
    chapters = plan["outline"]["volumes"][0]["chapters"]
    assert [chapter["requirement_ids"] for chapter in chapters] == [[], ["R_EVENT"], [], ["R_MILE"]]


def test_assignment_is_idempotent_when_contract_and_outline_do_not_change():
    plan = _four_chapter_plan()
    first = assign_requirements_to_outline(_typed_requirements(), plan)

    second = assign_requirements_to_outline(first, plan)

    assert second["version"] == first["version"]
    assert second["updated_at"] == first["updated_at"]


def test_chapter_evaluation_skips_planning_and_does_not_require_global_facts_verbatim():
    requirements = assign_requirements_to_outline(_typed_requirements(), _four_chapter_plan())

    report = evaluate_requirements_against_text(
        "The narrator watches the rain. The prose never restates the planning notes.",
        requirements,
        chapter_num=1,
        total_chapters=4,
    )

    results = {item["subtask_id"]: item for item in report["results"]}
    assert "R_PLAN" not in results
    assert "R_EVENT" not in results
    assert results["R_RULE"]["status"] == "passed"
    assert results["R_RULE"]["missing"] == []
    assert results["R_RULE"]["not_required_in_chapter"] == ["first-person narration"]
    assert results["R_PREF"]["status"] == "needs_review"
    assert report["overall_status"] == "needs_review"


def test_forbidden_book_invariant_is_a_hard_chapter_failure():
    report = evaluate_requirements_against_text(
        "The scene suddenly uses head-hopping.",
        _typed_requirements(),
        chapter_num=1,
        total_chapters=4,
    )

    result = next(item for item in report["results"] if item["subtask_id"] == "R_RULE")
    assert result["kind"] == "book_invariant"
    assert result["hard_gate"] is True
    assert result["status"] == "failed"
    assert report["failed_count"] == 1


def test_active_requirements_keep_global_guidance_but_exclude_planning_items():
    requirements = assign_requirements_to_outline(_typed_requirements(), _four_chapter_plan())

    ids = [item["id"] for item in active_requirements(requirements, 2, 4)]

    assert ids == ["R_RULE", "R_PREF", "R_EVENT"]


def test_assign_requirements_to_outline_creates_chapter_contract():
    requirements = _requirements()
    plan = {
        "title": "Closure Test",
        "outline": {
            "volumes": [
                {"chapters": [{"chapter_num": 1, "title": "A clue"}, {"chapter_num": 2, "title": "Aftermath"}]}
            ]
        },
    }

    result = assign_requirements_to_outline(requirements, plan)

    assert result["subtasks"][0]["chapter_numbers"] == [1]
    assert plan["outline"]["volumes"][0]["chapters"][0]["requirement_ids"] == ["R001"]
    assert plan["outline"]["volumes"][0]["chapters"][1]["requirement_ids"] == []
    assert result["schema_version"] >= 1
    assert result["version"] >= 1


def test_requirement_evaluator_returns_evidence_and_forbidden_failures():
    report = evaluate_requirements_against_text(
        "The hidden ledger appears in chapter one, but a magic reset erases the clue.",
        _requirements(),
        chapter_num=1,
    )

    item = report["results"][0]
    assert report["overall_status"] == "failed"
    assert item["subtask_id"] == "R001"
    assert item["forbidden"] == ["magic reset"]
    assert item["evidence"]


class _InstructionProbe(GenerationMixin):
    def __init__(self):
        self._req_store = type("Store", (), {"get": lambda self, key: _requirements()})()


def test_generation_instruction_keeps_full_requirement_contract():
    probe = _InstructionProbe()
    instruction = probe.build_requirements_instruction(
        "Closure Test",
        chapter_num=1,
        plan={"title": "Closure Test", "outline": {"total_chapters": 2}},
    )

    assert "R001" in instruction
    assert "hidden ledger" in instruction
    assert "magic reset" in instruction
    assert "concrete clue" in instruction
    assert "changes the protagonist" in instruction


class _TypedInstructionProbe(GenerationMixin):
    def __init__(self):
        self._req_store = type("Store", (), {"get": lambda self, key: _typed_requirements()})()


def test_generation_instruction_separates_hard_rules_events_and_soft_preferences():
    probe = _TypedInstructionProbe()

    instruction = probe.build_requirements_instruction(
        "Typed Contract",
        chapter_num=2,
        plan={"title": "Typed Contract", "outline": {"total_chapters": 4}},
    )

    assert "R_PLAN" not in instruction
    assert "R_RULE" in instruction
    assert "R_EVENT" in instruction
    assert "R_PREF" in instruction
    assert "HARD CHAPTER COMMITMENTS" in instruction
    assert "BOOK INVARIANTS" in instruction
    assert "SOFT PREFERENCES" in instruction


class _CopyStore:
    def __init__(self, value):
        self.value = value

    def get(self, _key):
        import copy
        return copy.deepcopy(self.value)

    def set(self, _key, value):
        import copy
        self.value = copy.deepcopy(value)


class _Supervisor:
    def supervise(self, requirements, _plan):
        requirements["subtasks"][0]["status"] = "failed"
        requirements["subtasks"][0]["feedback"] = "fresh evidence is missing"
        return {"overall_status": "failed", "failed_count": 1, "results": []}

    def get_failed_feedback(self, requirements):
        return "|".join(item.get("feedback", "") for item in requirements["subtasks"])


class _RequirementsProbe(RequirementsMixin):
    def __init__(self):
        self._req_store = _CopyStore(_requirements())
        self.requirement_supervisor = _Supervisor()

    def get_novel(self, _novel_id):
        return {"title": "Closure Test", "outline": {"total_chapters": 2}}


def test_supervision_persists_status_and_feedback_for_next_loop():
    probe = _RequirementsProbe()

    report = probe.supervise_requirements("novel-1")

    assert report["overall_status"] == "failed"
    saved = probe._req_store.get("novel-1")
    assert saved["subtasks"][0]["status"] == "failed"
    assert "fresh evidence" in saved["subtasks"][0]["feedback"]


class _LoopOutline:
    def __init__(self):
        self.feedback = ""

    async def process_feedback(self, feedback, plan, _planner):
        self.feedback = feedback
        yield {"type": "done", "plan": plan}


class _LoopPlanner:
    @staticmethod
    def repair_outline(outline):
        return outline


class _LoopMemory:
    def __init__(self, root):
        self.root = root

    def get_novel_dir(self, _novel_id):
        return str(self.root)


def test_verify_loop_reads_feedback_after_supervisor_mutation(tmp_path):
    probe = _RequirementsProbe()
    probe.outline_interactive = _LoopOutline()
    probe.planner = _LoopPlanner()
    probe.memory = _LoopMemory(tmp_path)
    probe.save_character_bible = lambda *_args: None

    async def collect():
        return [event async for event in probe.verify_and_fix_loop("novel-1", max_iterations=1)]

    events = __import__("asyncio").run(collect())

    assert "fresh evidence is missing" in probe.outline_interactive.feedback
    assert any(event.get("type") == "done" for event in events)
