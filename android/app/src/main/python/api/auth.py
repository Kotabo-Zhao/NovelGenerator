"""FastAPI authentication dependency for signed WeChat sessions."""

from typing import Optional

from fastapi import Header, HTTPException

from core.auth import AuthError, WeChatSessionService


session_service = WeChatSessionService()


def require_owner(authorization: Optional[str] = Header(default=None)) -> str:
    """Return the authenticated pseudonymous owner id from a Bearer token."""

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="需要微信登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization[7:].strip()
    try:
        claims = session_service.verify_token(token)
    except AuthError as exc:
        raise HTTPException(
            status_code=401,
            detail="登录状态无效或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    return claims["sub"]
