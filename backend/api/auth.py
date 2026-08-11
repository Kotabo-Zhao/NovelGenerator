"""FastAPI authentication dependency for signed WeChat sessions."""

from typing import Optional

from fastapi import Header, HTTPException

from core.auth import AuthError, WeChatSessionService


session_service = WeChatSessionService()


def owner_id_from_authorization(authorization: Optional[str]) -> str:
    """Validate a Bearer header and return the pseudonymous owner id."""
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthError("missing bearer token")
    token = authorization[7:].strip()
    if not token:
        raise AuthError("missing bearer token")
    claims = session_service.verify_token(token)
    return claims["sub"]


def require_owner(authorization: Optional[str] = Header(default=None)) -> str:
    """Return the authenticated pseudonymous owner id from a Bearer token."""

    try:
        return owner_id_from_authorization(authorization)
    except AuthError as exc:
        raise HTTPException(
            status_code=401,
            detail="登录状态无效或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
