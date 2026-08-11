"""Tests for the WeChat session adapter without network access."""

import base64
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from core.auth import AuthError, WeChatSessionService


class WeChatSessionServiceTests(unittest.TestCase):
    def setUp(self):
        self.now = 1_700_000_000
        self.service = WeChatSessionService(
            app_id="wx-test-app",
            app_secret="test-app-secret",
            signing_secret="a" * 48,
            session_ttl_seconds=3600,
            clock=lambda: self.now,
        )

    def test_token_round_trip_returns_pseudonymous_owner(self):
        token = self.service.issue_token("openid-123")
        claims = self.service.verify_token(token)

        self.assertTrue(claims["sub"].startswith("wx_"))
        self.assertNotIn("openid-123", token)
        self.assertEqual(claims["exp"], self.now + 3600)
        self.assertEqual(claims["sub"], self.service.owner_id_for_openid("openid-123"))

    def test_tampered_token_is_rejected(self):
        token = self.service.issue_token("openid-123")
        payload, signature = token.split(".")
        tampered_payload = base64.urlsafe_b64encode(
            json.dumps({"sub": "wx_other", "exp": self.now + 3600}).encode("utf-8")
        ).decode("ascii").rstrip("=")

        with self.assertRaises(AuthError):
            self.service.verify_token(f"{tampered_payload}.{signature}")

    def test_expired_token_is_rejected(self):
        token = self.service.issue_token("openid-123")
        self.now += 3601

        with self.assertRaises(AuthError):
            self.service.verify_token(token)

    def test_wechat_code_exchange_is_injected_and_checked(self):
        seen = {}

        def fake_http_get(url):
            seen["url"] = url
            return b'{"errcode":0,"openid":"openid-123","session_key":"not-persisted"}'

        service = WeChatSessionService(
            app_id="wx-test-app",
            app_secret="test-app-secret",
            signing_secret="a" * 48,
            http_get=fake_http_get,
        )
        result = service.exchange_code("code-001")

        self.assertEqual(result["openid"], "openid-123")
        self.assertNotIn("session_key", result)
        self.assertIn("wx-test-app", seen["url"])
        self.assertIn("code-001", seen["url"])

    def test_missing_production_configuration_fails_closed(self):
        service = WeChatSessionService()

        with self.assertRaises(AuthError):
            service.issue_token("openid-123")

        with self.assertRaises(AuthError):
            service.exchange_code("code-001")


if __name__ == "__main__":
    unittest.main()
