from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ANDROID = ROOT / "android" / "app"


def test_debug_apk_is_explicitly_internal_and_release_is_strict():
    gradle = (ANDROID / "build.gradle.kts").read_text(encoding="utf-8")
    assert 'versionNameSuffix = "-internal"' in gradle
    assert 'buildConfigField("boolean", "INTERNAL_BUILD", "true")' in gradle
    assert 'buildConfigField("boolean", "INTERNAL_BUILD", "false")' in gradle


def test_internal_build_passes_mode_to_embedded_server():
    activity = (ANDROID / "src/main/java/com/novelgen/app/MainActivity.kt").read_text(encoding="utf-8")
    service = (ANDROID / "src/main/java/com/novelgen/app/ServerService.kt").read_text(encoding="utf-8")
    runner = (ANDROID / "src/main/python/server_runner.py").read_text(encoding="utf-8")
    assert 'if (apiKey.isBlank() && !BuildConfig.INTERNAL_BUILD)' in activity
    assert 'putExtra("internal_build", BuildConfig.INTERNAL_BUILD)' in activity
    assert 'getBooleanExtra("internal_build", false)' in service
    assert 'callAttr("start_server", apiKey, "127.0.0.1", 8899, logDir, internalBuild)' in service
    assert 'os.environ["NOVEL_GENERATOR_INTERNAL"] = "true"' in runner


def test_internal_build_exposes_key_entry_and_refreshes_running_client():
    activity = (ANDROID / "src/main/java/com/novelgen/app/MainActivity.kt").read_text(encoding="utf-8")
    key_activity = (ANDROID / "src/main/java/com/novelgen/app/ApiKeyActivity.kt").read_text(encoding="utf-8")
    service = (ANDROID / "src/main/java/com/novelgen/app/ServerService.kt").read_text(encoding="utf-8")
    runner = (ANDROID / "src/main/python/server_runner.py").read_text(encoding="utf-8")
    assert 'if (!frontendLoaded && !serverStartRequested' in activity
    assert 'private var serverStartRequested = false' in activity
    assert 'putExtra("update_only", true)' in key_activity
    assert 'getBooleanExtra("update_only", false)' in service
    assert 'callAttr("update_api_key", apiKey)' in service
    assert 'engine.client.api_key = api_key' in runner


def test_main_screen_has_no_fixed_key_banner_or_toolbar():
    """v2.7: 顶部不再固定显示 API Key 配置横幅与工具栏标题（设置收纳进前端设置页）。"""
    layout = (ANDROID / "src/main/res/layout/activity_main.xml").read_text(encoding="utf-8")
    activity = (ANDROID / "src/main/java/com/novelgen/app/MainActivity.kt").read_text(encoding="utf-8")
    strings = (ANDROID / "src/main/res/values/strings.xml").read_text(encoding="utf-8")
    menu = (ANDROID / "src/main/res/menu/main_menu.xml").read_text(encoding="utf-8")
    assert 'android:id="@+id/api_key_banner"' not in layout
    assert 'android:id="@+id/config_api_key_button"' not in layout
    assert 'binding.configApiKeyButton.setOnClickListener' not in activity
    assert 'Toolbar' not in layout
    assert 'setSupportActionBar' not in activity
    assert 'menu_config' not in menu
    assert "小说工坊" not in strings
