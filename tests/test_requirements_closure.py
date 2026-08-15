from backend.core.requirements_contract import (
    assign_requirements_to_outline,
    evaluate_requirements_against_text,
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
