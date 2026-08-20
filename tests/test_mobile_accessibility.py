from pathlib import Path


def _frontend() -> str:
    return (Path(__file__).parents[1] / "web" / "index.html").read_text(encoding="utf-8")


def test_mobile_primary_controls_have_44px_touch_targets():
    source = _frontend()

    assert ".btn{padding:8px 14px;font-size:13px;min-height:44px}" in source
    assert ".btn-xs,.btn-sm{min-width:44px;min-height:44px" in source
    assert ".wb-tab,.ws-ch,.batch-fold summary{min-height:44px}" in source
    assert "input,select{min-height:44px}" in source
    assert ".voice-btn{width:44px;height:44px}" in source


def test_mobile_bottom_navigation_uses_keyboard_accessible_buttons():
    source = _frontend()

    assert '<div class="bn-item"' not in source
    assert source.count('<button type="button" class="bn-item"') == 5
    assert 'aria-label="书架"' in source
    assert 'aria-label="互动剧场"' in source


def test_icon_only_workbench_actions_have_accessible_names():
    source = _frontend()

    for label in (
        "打开设置",
        "打开灵感箱",
        "搜索作品",
        "全文检索",
        "世界圣经",
        "创作统计",
        "设定扫描",
        "版本历史",
        "更多工具",
    ):
        assert f'aria-label="{label}"' in source
