"""回归：桌面兼容界面不得把延期工具塞进小说主流程。"""

from pathlib import Path


WEB = Path(__file__).resolve().parents[1] / "web" / "index.html"


def _section(source: str, start: str, end: str) -> str:
    start_at = source.index(start)
    end_at = source.index(end, start_at)
    return source[start_at:end_at]


def test_sidebar_only_exposes_core_workflow():
    source = WEB.read_text(encoding="utf-8")
    nav = _section(source, '<nav class="topnav">', "</nav>")

    for label in ("书架", "开卷", "落笔", "付梓"):
        assert label in nav
    assert "追风" not in nav
    assert "openTrends" not in nav


def test_create_page_has_one_generation_entry():
    source = WEB.read_text(encoding="utf-8")
    create = _section(source, '<section v-if="currentView===\'create\'"', "</section>")

    assert "✨ 生成大纲" in create
    assert "小红书爆款短篇" not in create
    assert "xiaohongshu" not in create


def test_write_page_does_not_render_deferred_tool_tabs():
    source = WEB.read_text(encoding="utf-8")
    write_header = _section(
        source,
        '<section v-if="currentView===\'write\'"',
        '<!-- ══════════ 导出视图 ══════════ -->',
    )

    assert 'class="sg-tabs"' not in write_header
    assert "剧情图谱" not in write_header
    assert "data-xhs" not in write_header


def test_removed_trend_view_has_no_frontend_route_or_state():
    source = WEB.read_text(encoding="utf-8")

    assert "currentView==='trends'" not in source
    assert "const trendData" not in source
    assert "const xhsTemplates" not in source
