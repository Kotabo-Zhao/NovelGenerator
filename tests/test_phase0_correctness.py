"""Regression coverage for phase-0 release blockers from the 2026-08-20 audit."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path


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
