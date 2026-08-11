"""原生微信小程序工程结构回归测试。"""
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "miniprogram"
PAGES = ("shelf", "create", "write", "profile")


class MiniProgramStructureTests(unittest.TestCase):
    def test_app_config_declares_four_core_tabs(self):
        config = json.loads((ROOT / "app.json").read_text(encoding="utf-8"))
        self.assertEqual(config["pages"][:4], [f"pages/{name}/index" for name in PAGES])
        self.assertEqual(
            [item["pagePath"] for item in config["tabBar"]["list"]],
            [f"pages/{name}/index" for name in PAGES],
        )

    def test_each_core_page_has_native_files(self):
        for name in PAGES:
            with self.subTest(page=name):
                page_dir = ROOT / "pages" / name
                for suffix in ("js", "json", "wxml", "wxss"):
                    self.assertTrue((page_dir / f"index.{suffix}").is_file())
                json.loads((page_dir / "index.json").read_text(encoding="utf-8"))

    def test_request_layer_has_no_auth_token_storage(self):
        request_source = (ROOT / "utils" / "api.js").read_text(encoding="utf-8")
        self.assertNotIn("setStorageSync('token'", request_source)
        self.assertNotIn('setStorageSync("token"', request_source)

    def test_request_layer_has_in_memory_session_and_secure_v1_usage(self):
        request_source = (ROOT / "utils" / "api.js").read_text(encoding="utf-8")
        app_source = (ROOT / "app.js").read_text(encoding="utf-8")
        create_source = (ROOT / "pages" / "create" / "index.js").read_text(encoding="utf-8")
        write_source = (ROOT / "pages" / "write" / "index.js").read_text(encoding="utf-8")

        self.assertIn("/api/auth/wechat-login", request_source)
        self.assertIn("Authorization", request_source)
        self.assertIn("accessToken", app_source)
        self.assertIn("/v1/novels", create_source)
        self.assertIn("Idempotency-Key", create_source)
        self.assertIn("/v1/jobs/", write_source)
        self.assertIn("/v1/usage", (ROOT / "pages" / "profile" / "index.js").read_text(encoding="utf-8"))
        self.assertIn("/chapters/", write_source)
        self.assertIn("/draft", write_source)

    def test_core_pages_have_real_loading_error_and_primary_action_states(self):
        shelf_wxml = (ROOT / "pages" / "shelf" / "index.wxml").read_text(encoding="utf-8")
        create_wxml = (ROOT / "pages" / "create" / "index.wxml").read_text(encoding="utf-8")
        create_js = (ROOT / "pages" / "create" / "index.js").read_text(encoding="utf-8")
        write_wxml = (ROOT / "pages" / "write" / "index.wxml").read_text(encoding="utf-8")
        write_js = (ROOT / "pages" / "write" / "index.js").read_text(encoding="utf-8")
        profile_wxml = (ROOT / "pages" / "profile" / "index.wxml").read_text(encoding="utf-8")
        profile_js = (ROOT / "pages" / "profile" / "index.js").read_text(encoding="utf-8")

        self.assertIn("hero-card", shelf_wxml)
        self.assertIn("重新加载", shelf_wxml)
        self.assertIn("skeletons", shelf_wxml)
        self.assertIn("useExample", create_wxml)
        self.assertIn("targetWords < 1000", create_js)
        self.assertIn("sticky-actions", write_wxml)
        self.assertIn("retryCurrent", write_wxml)
        self.assertIn("statusText", write_js)
        self.assertIn("快速入口", profile_wxml)
        self.assertNotIn("即将开放", profile_wxml)
        self.assertNotIn("onTap", profile_js)
        self.assertIn("agreePrivacy", profile_js)
        self.assertIn("deleteAccount", profile_js)


if __name__ == "__main__":
    unittest.main()
