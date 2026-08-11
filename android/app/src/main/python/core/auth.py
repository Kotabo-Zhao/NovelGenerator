"""WeChat login exchange and signed owner sessions.

This module does not invent an identity when production configuration is
missing.  The Mini Program sends a temporary ``code``; the backend exchanges
it with WeChat, derives a stable pseudonymous owner id, and returns a short
signed session token.  ``session_key`` never leaves this module or gets
persisted.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlencode
from urllib.request import urlopen


class AuthError(RuntimeError):
    """Expected authentication/configuration failure safe to expose to clients."""


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class WeChatSessionService:
    """Exchange WeChat login codes and issue/verify HMAC-signed sessions."""

    EXCHANGE_URL = "https://api.weixin.qq.com/sns/jscode2session"
    MAX_CODE_LENGTH = 512
    MAX_TOKEN_LENGTH = 4096

    def __init__(
        self,
        *,
        app_id: Optional[str] = None,
        app_secret: Optional[str] = None,
        signing_secret: Optional[str] = None,
        session_ttl_seconds: Optional[int] = None,
        clock: Callable[[], float] = time.time,
        http_get: Optional[Callable[[str], bytes]] = None,
    ):
        self.app_id = app_id if app_id is not None else os.getenv("WECHAT_APP_ID", "")
        self.app_secret = (
            app_secret if app_secret is not None else os.getenv("WECHAT_APP_SECRET", "")
        )
        self.signing_secret = (
            signing_secret
            if signing_secret is not None
            else os.getenv("WECHAT_SESSION_SECRET", "")
        )
        raw_ttl = (
            session_ttl_seconds
            if session_ttl_seconds is not None
            else os.getenv("WECHAT_SESSION_TTL_SECONDS", "604800")
        )
        try:
            self.session_ttl_seconds = int(raw_ttl)
        except (TypeError, ValueError) as exc:
            raise ValueError("WECHAT_SESSION_TTL_SECONDS must be an integer") from exc
        if not 300 <= self.session_ttl_seconds <= 30 * 24 * 3600:
            raise ValueError("WECHAT_SESSION_TTL_SECONDS must be between 300 and 2592000")
        self.clock = clock
        self.http_get = http_get or self._default_http_get

    @staticmethod
    def _default_http_get(url: str) -> bytes:
        with urlopen(url, timeout=8) as response:
            return response.read(64 * 1024)

    @staticmethod
    def _required_text(value: Any, field: str, max_length: int) -> str:
        if not isinstance(value, str):
            raise AuthError(f"{field} invalid")
        normalized = value.strip()
        if not normalized or len(normalized) > max_length:
            raise AuthError(f"{field} invalid")
        return normalized

    def _require_configured(self) -> None:
        if not self.app_id or not self.app_secret:
            raise AuthError("WeChat login is not configured")
        if not isinstance(self.signing_secret, str) or len(self.signing_secret) < 32:
            raise AuthError("WeChat session signing secret is not configured")

    def owner_id_for_openid(self, openid: str) -> str:
        self._require_configured()
        normalized_openid = self._required_text(openid, "openid", 256)
        digest = hmac.new(
            self.signing_secret.encode("utf-8"),
            f"wechat:{normalized_openid}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return f"wx_{digest}"

    def exchange_code(self, code: str) -> Dict[str, str]:
        """Exchange a temporary code without returning/persisting ``session_key``."""

        self._require_configured()
        normalized_code = self._required_text(code, "code", self.MAX_CODE_LENGTH)
        query = urlencode(
            {
                "appid": self.app_id,
                "secret": self.app_secret,
                "js_code": normalized_code,
                "grant_type": "authorization_code",
            }
        )
        try:
            raw = self.http_get(f"{self.EXCHANGE_URL}?{query}")
            payload = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise AuthError("WeChat login service is unavailable") from exc
        if not isinstance(payload, dict):
            raise AuthError("WeChat login response is invalid")
        errcode = payload.get("errcode", 0)
        if errcode not in (0, "0"):
            raise AuthError("WeChat login code is invalid or expired")
        openid = self._required_text(payload.get("openid"), "openid", 256)
        result = {"openid": openid}
        unionid = payload.get("unionid")
        if isinstance(unionid, str) and unionid.strip():
            result["unionid"] = unionid.strip()[:256]
        return result

    def issue_token(self, openid: str) -> str:
        self._require_configured()
        now = int(self.clock())
        claims = {
            "v": 1,
            "sub": self.owner_id_for_openid(openid),
            "iat": now,
            "exp": now + self.session_ttl_seconds,
        }
        encoded_payload = _b64encode(
            json.dumps(claims, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            .encode("utf-8")
        )
        signature = hmac.new(
            self.signing_secret.encode("utf-8"),
            encoded_payload.encode("ascii"),
            hashlib.sha256,
        ).digest()
        return f"{encoded_payload}.{_b64encode(signature)}"

    def verify_token(self, token: str) -> Dict[str, Any]:
        self._require_configured()
        if not isinstance(token, str) or not 0 < len(token) <= self.MAX_TOKEN_LENGTH:
            raise AuthError("session token invalid")
        parts = token.split(".")
        if len(parts) != 2:
            raise AuthError("session token invalid")
        encoded_payload, encoded_signature = parts
        try:
            expected_signature = hmac.new(
                self.signing_secret.encode("utf-8"),
                encoded_payload.encode("ascii"),
                hashlib.sha256,
            ).digest()
            actual_signature = _b64decode(encoded_signature)
            if not hmac.compare_digest(actual_signature, expected_signature):
                raise AuthError("session token invalid")
            claims = json.loads(_b64decode(encoded_payload).decode("utf-8"))
        except AuthError:
            raise
        except (ValueError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
            raise AuthError("session token invalid") from exc
        if not isinstance(claims, dict) or claims.get("v") != 1:
            raise AuthError("session token invalid")
        subject = claims.get("sub")
        expires_at = claims.get("exp")
        issued_at = claims.get("iat")
        if (
            not isinstance(subject, str)
            or not subject.startswith("wx_")
            or not isinstance(expires_at, int)
            or isinstance(expires_at, bool)
            or not isinstance(issued_at, int)
            or isinstance(issued_at, bool)
            or expires_at <= int(self.clock())
            or issued_at > expires_at
        ):
            raise AuthError("session token expired or invalid")
        return claims
