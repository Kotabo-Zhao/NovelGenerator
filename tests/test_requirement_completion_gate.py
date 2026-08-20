import hashlib
from pathlib import Path

from backend.core.mixins.generation import GenerationMixin, _hard_requirement_failures


class _Memory:
    def __init__(self):
        self.state = {
            "completed_chapters": [1, 2],
            "current_chapter": 2,
            "chapter_statuses": {},
        }
        self.chapters = {}
        self.drafts = {}
        self.removed_incomplete = []
        self.deleted_drafts = []

    def write_chapter_draft(self, novel_id, chapter_num, content):
        self.drafts[(novel_id, chapter_num)] = content

    def delete_chapter_draft(self, novel_id, chapter_num):
        self.deleted_drafts.append((novel_id, chapter_num))
        self.drafts.pop((novel_id, chapter_num), None)

    def remove_incomplete_chapter(self, novel_id, chapter_num):
        self.removed_incomplete.append((novel_id, chapter_num))
        return True

    def chapter_exists(self, novel_id, chapter_num):
        return (novel_id, chapter_num) in self.chapters

    def save_chapter(self, novel_id, chapter_num, content):
        self.chapters[(novel_id, chapter_num)] = content

    def repair_state(self, _novel_id):
        return {"state": dict(self.state)}

    def get_novel_state(self, _novel_id):
        return dict(self.state)

    def save_novel_state(self, _novel_id, state):
        self.state = state


class _Probe(GenerationMixin):
    def __init__(self):
        self.memory = _Memory()


def _failed_report():
    return {
        "hard_failure_count": 1,
        "results": [
            {
                "subtask_id": "R_EVENT",
                "title": "必须拆开密信",
                "status": "failed",
                "hard_gate": True,
            },
            {
                "subtask_id": "R_PREF",
                "title": "语言克制",
                "status": "failed",
                "hard_gate": False,
            },
        ],
    }


def test_only_failed_hard_gate_requirements_block_completion():
    assert [item["subtask_id"] for item in _hard_requirement_failures(_failed_report())] == ["R_EVENT"]
    assert _hard_requirement_failures({"results": [{"status": "needs_review", "hard_gate": True}]}) == []


def test_finalizer_preserves_failed_candidate_as_revision_draft():
    probe = _Probe()
    text = "林岚看完信，却没有拆开封蜡。"
    formatted = "# 第2章 密室来信\n\n" + text

    result = probe._finalize_chapter_candidate(
        "novel-1",
        2,
        text,
        formatted,
        _failed_report(),
        {"score": 82},
    )

    assert result["status"] == "needs_revision"
    assert probe.memory.drafts[("novel-1", 2)] == formatted
    assert ("novel-1", 2) in probe.memory.removed_incomplete
    assert 2 not in probe.memory.state["completed_chapters"]
    status = probe.memory.state["chapter_statuses"]["2"]
    assert status["status"] == "needs_revision"
    assert status["requirement_ids"] == ["R_EVENT"]
    assert status["text_sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_finalizer_is_the_single_completed_chapter_committer():
    probe = _Probe()
    probe.memory.state["completed_chapters"] = [1]
    text = "林岚拆开密信，看见父亲留下的坐标。"
    formatted = "# 第2章 密室来信\n\n" + text

    result = probe._finalize_chapter_candidate(
        "novel-1",
        2,
        text,
        formatted,
        {"hard_failure_count": 0, "results": []},
        {"score": 88},
    )

    assert result["status"] == "completed"
    assert probe.memory.chapters[("novel-1", 2)] == formatted
    assert probe.memory.state["completed_chapters"] == [1, 2]
    assert probe.memory.state["chapter_statuses"]["2"]["status"] == "completed"
    assert ("novel-1", 2) in probe.memory.deleted_drafts


def test_post_generation_validation_error_keeps_the_candidate_for_revision():
    probe = _Probe()

    result = probe._preserve_generation_failure(
        "novel-1",
        2,
        "林岚拆开密信，却在句子中途停下",
        "# 第2章 密室来信\n\n林岚拆开密信，却在句子中途停下",
        ValueError("chapter does not end with punctuation"),
    )

    assert result["status"] == "needs_revision"
    assert "chapter does not end with punctuation" in result["reason"]
    assert probe.memory.drafts[("novel-1", 2)].startswith("# 第2章")


def test_failed_regeneration_keeps_the_previously_published_chapter():
    probe = _Probe()
    published = "# 第2章 旧版\n\n这是已发布的稳定版本。"
    probe.memory.chapters[("novel-1", 2)] = published

    result = probe._record_revision_candidate(
        "novel-1",
        2,
        "这是未通过的新版本。",
        "# 第2章 新版\n\n这是未通过的新版本。",
        reason="P0 需求未满足",
    )

    assert probe.memory.chapters[("novel-1", 2)] == published
    assert 2 in probe.memory.state["completed_chapters"]
    assert result["published_chapter_preserved"] is True


def test_both_draft_strategies_call_the_shared_finalizer_before_done():
    source = (
        Path(__file__).parents[1] / "backend" / "core" / "mixins" / "generation.py"
    ).read_text(encoding="utf-8")

    assert source.count("self._finalize_chapter_candidate(") == 2
    assert source.count("self.memory.save_chapter(") == 1
    assert "GENERATION_IN_PROGRESS" not in source
    assert "请检查网络连接或稍后重试" not in source
    assert '"type": "needs_revision"' in source


def test_revision_draft_is_exposed_without_pretending_it_is_published():
    root = Path(__file__).parents[1]
    router_source = (root / "backend" / "api" / "routers" / "novels.py").read_text(encoding="utf-8")
    frontend_source = (root / "web" / "index.html").read_text(encoding="utf-8")

    get_chapter_source = router_source[
        router_source.index("async def get_chapter("):
        router_source.index("async def chapter_exists(")
    ]
    assert "read_chapter_draft" in get_chapter_source
    assert '"is_published"' in get_chapter_source
    assert "d.draft" in frontend_source
    assert "writing.revisionReason" in frontend_source
