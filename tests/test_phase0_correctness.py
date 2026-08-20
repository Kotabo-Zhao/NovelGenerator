"""Regression coverage for phase-0 release blockers from the 2026-08-20 audit."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

from backend.core.shared_memory import normalize_chapter_paragraphs


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
