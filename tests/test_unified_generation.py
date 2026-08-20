from pathlib import Path

from backend.core.mixins.generation import choose_draft_strategy


def test_ready_chapter_contract_uses_atomic_drafting_internally():
    chapter = {
        "contract_status": "ready",
        "scene_beats": [
            {"key_action": "主角拆开信封"},
            {"key_action": "主角确认坐标"},
            {"key_action": "主角决定前往码头"},
        ],
    }

    assert choose_draft_strategy(chapter) == "atomic"


def test_legacy_or_batch_generation_uses_standard_drafting():
    assert choose_draft_strategy({"summary": "只有一句旧大纲"}) == "standard"
    assert choose_draft_strategy(
        {"contract_status": "ready", "scene_beats": [{"key_action": "事件"}] * 3},
        batch_mode=True,
    ) == "standard"


def test_frontend_exposes_one_generation_entry_without_implementation_toggle():
    html = (Path(__file__).parents[1] / "web" / "index.html").read_text(encoding="utf-8")

    assert "useAtomicMode" not in html
    assert "'/api/novels/generate/atomic'" not in html
    assert "标准</span>" not in html
    assert "原子化</span>" not in html
    assert "const endpoint = '/api/novels/generate'" in html


def test_primary_api_uses_the_unified_generation_stream():
    router = (
        Path(__file__).parents[1] / "backend" / "api" / "routers" / "novels.py"
    ).read_text(encoding="utf-8")

    primary = router[router.index('@router.post("/api/novels/generate")'):]
    primary = primary[:primary.index('@router.post("/api/novels/generate/atomic")')]
    assert "unified_generate_chapter_stream" in primary
