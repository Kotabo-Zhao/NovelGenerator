"""WeChat Mini Program login endpoint."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.auth import AuthError

from ..auth import session_service


router = APIRouter()


class WeChatLoginRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=512)


@router.post("/api/auth/wechat-login")
async def wechat_login(req: WeChatLoginRequest):
    """Exchange ``wx.login`` code for a backend-owned signed session token."""

    try:
        identity = session_service.exchange_code(req.code)
        access_token = session_service.issue_token(identity["openid"])
    except AuthError as exc:
        message = str(exc)
        if "not configured" in message:
            raise HTTPException(status_code=503, detail="微信登录尚未配置") from exc
        if "unavailable" in message:
            raise HTTPException(status_code=503, detail="微信登录服务暂时不可用") from exc
        raise HTTPException(status_code=401, detail="微信登录失败，请重新登录") from exc

    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": session_service.session_ttl_seconds,
    }
