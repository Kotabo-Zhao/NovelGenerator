"""FastAPI authentication dependency for signed WeChat sessions."""

import os
from typing import Optional

from fastapi import Header, HTTPException

from core.auth import AuthError, WeChatSessionService

from .deps import account_store


session_service = WeChatSessionService()


def is_internal_version() -> bool:
    """Whether this process is the private, local/internal build."""
    return os.getenv("NOVEL_GENERATOR_INTERNAL", "false").strip().lower() in {
        "1", "true", "yes", "on",
    }


def owner_id_from_authorization(authorization: Optional[str]) -> str:
    """Validate a Bearer header and return the pseudonymous owner id."""
    if is_internal_version():
        return "internal-local-owner"
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthError("missing bearer token")
    token = authorization[7:].strip()
    if not token:
        raise AuthError("missing bearer token")
    claims = session_service.verify_token(token)
    owner_id = claims["sub"]
    account = account_store.get(owner_id)
    if account is None:
        account = account_store.ensure_user(owner_id)
    if account.get("deleted_at") is not None or account_store.is_revoked(owner_id, claims["iat"]):
        raise AuthError("session revoked")
    return owner_id


def require_consented_owner(authorization: Optional[str] = Header(default=None)) -> str:
    owner_id = require_owner(authorization)
    if is_internal_version():
        return owner_id
    required_version = os.getenv("WECHAT_PRIVACY_VERSION", "v1")
    if not account_store.has_consent(owner_id, required_version):
        raise HTTPException(status_code=403, detail="请先同意隐私政策")
    return owner_id


def require_owner(authorization: Optional[str] = Header(default=None)) -> str:
    """Return the authenticated pseudonymous owner id from a Bearer token."""

    if is_internal_version():
        return "internal-local-owner"
    try:
        return owner_id_from_authorization(authorization)
    except AuthError as exc:
        raise HTTPException(
            status_code=401,
            detail="登录状态无效或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
