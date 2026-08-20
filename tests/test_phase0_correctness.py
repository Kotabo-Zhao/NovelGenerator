"""Regression coverage for phase-0 release blockers from the 2026-08-20 audit."""

from __future__ import annotations

import importlib.util
import inspect
import re
from pathlib import Path

from backend.core.mixins.generation import GenerationMixin
from backend.core.shared_memory import normalize_chapter_paragraphs
from backend.core.shared_memory import SharedMemoryManager


ROOT = Path(__file__).resolve().parents[1]


def test_relative_novels_dir_is_resolved_from_project_root(monkeypatch):
    """Starting from backend/ must not silently select backend/novels."""

    monkeypatch.setenv("NOVELS_DIR", "./novels")
    monkeypatch.chdir(ROOT / "backend")

    spec = importlib.util.spec_from_file_location(
        "phase0_config_probe", ROOT / "backend" / "config.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert Path(module.NOVELS_DIR) == (ROOT / "novels").resolve()


def test_desktop_app_shell_stacks_topbar_above_stage():
    """The top bar must not become the first column of a horizontal app shell."""

    source = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    match = re.search(r"#app\{([^}]+)\}", source)

    assert match, "base #app rule is missing"
    declarations = match.group(1).replace(" ", "").lower()
    assert "display:flex" not in declarations or "flex-direction:column" in declarations


def test_paragraph_normalizer_preserves_semantic_breaks_and_long_dialogue():
    """Formatting cleanup must not repartition prose at arbitrary character counts."""

    dialogue = "「" + "这句话带着人物自己的停顿和节奏？" * 12 + "」"
    source = f"# 第一章\n\n{dialogue}\n\n他没有马上回答。"

    assert normalize_chapter_paragraphs(source) == source


def test_paragraph_normalizer_only_collapses_excess_blank_lines():
    source = "第一段。\n\n\n\n第二段。\n\n"

    assert normalize_chapter_paragraphs(source) == "第一段。\n\n第二段。"


def test_atomic_path_rebuilds_document_after_target_trim():
    source = inspect.getsource(GenerationMixin.atomic_generate_chapter_stream)
    trim_at = source.index("full_text = _trim_to_target")
    save_at = source.index("self.memory.save_chapter", trim_at)

    assert "formatted =" in source[trim_at:save_at]


def test_generation_paths_mark_completion_only_after_final_quality_report():
    for method in (
        GenerationMixin.atomic_generate_chapter_stream,
        GenerationMixin.generate_chapter_stream,
    ):
        source = inspect.getsource(method)
        final_report_at = source.rindex("_save_final_quality_report")
        completion_at = source.index('state["completed_chapters"]')

        assert completion_at > final_report_at


def test_state_repair_recomputes_total_words_from_published_chapters(tmp_path):
    memory = SharedMemoryManager(str(tmp_path))
    novel_id = "book"
    memory.save_chapter(novel_id, 1, "# 第一章\n\n第一段。")
    memory.save_chapter(novel_id, 2, "# 第二章\n\n第二段更长。")
    memory.save_novel_state(
        novel_id,
        {
            "completed_chapters": [1, 2],
            "current_chapter": 2,
            "total_words": 0,
        },
    )

    result = memory.repair_state(novel_id)

    assert result["repaired"] is True
    assert result["state"]["total_words"] == len("第一段。") + len("第二段更长。")


def test_generation_marker_keeps_draft_out_of_completed_state(tmp_path):
    memory = SharedMemoryManager(str(tmp_path))
    novel_id = "book"
    memory.save_novel_state(
        novel_id,
        {"completed_chapters": [], "current_chapter": 0, "total_words": 0},
    )
    document = "# 第一章\n\n正文内容。"

    memory.save_chapter(
        novel_id,
        1,
        document + "\n\n<!-- GENERATION_IN_PROGRESS -->",
    )

    assert memory.scan_chapters(novel_id) == []
    assert memory.get_novel_state(novel_id)["completed_chapters"] == []

    memory.save_chapter(novel_id, 1, document)
    result = memory.repair_state(novel_id)

    assert result["state"]["completed_chapters"] == [1]
    assert result["state"]["total_words"] == len("正文内容。")
