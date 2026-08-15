"""WeChat Mini Program login endpoint."""

import hashlib
import os
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core.auth import AuthError

from ..auth import is_internal_version, require_owner, session_service
from ..deps import account_store, engine, job_store, ownership_store, quota_store
from config import NOVELS_DIR


router = APIRouter()


class WeChatLoginRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=512)


class PrivacyConsentRequest(BaseModel):
    agreed: bool = True
    policy_version: str = Field(..., min_length=1, max_length=100)


@router.post("/api/auth/wechat-login")
async def wechat_login(req: WeChatLoginRequest):
    """Exchange ``wx.login`` code for a backend-owned signed session token."""

    try:
        identity = session_service.exchange_code(req.code)
        owner_id = session_service.owner_id_for_openid(identity["openid"])
        account = account_store.ensure_user(owner_id)
        if account.get("deleted_at") is not None:
            raise AuthError("account deleted")
        access_token = session_service.issue_token(identity["openid"])
    except AuthError as exc:
        message = str(exc)
        if "not configured" in message:
            raise HTTPException(status_code=503, detail="微信登录尚未配置") from exc
        if "unavailable" in message:
            raise HTTPException(status_code=503, detail="微信登录服务暂时不可用") from exc
        raise HTTPException(status_code=401, detail="微信登录失败，请重新登录") from exc

    required_version = os.getenv("WECHAT_PRIVACY_VERSION", "v1")
    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": session_service.session_ttl_seconds,
        "consent_required": not account_store.has_consent(owner_id, required_version),
        "privacy_version": required_version,
    }


@router.get("/api/auth/me")
async def auth_me(owner_id: str = Depends(require_owner)):
    required_version = os.getenv("WECHAT_PRIVACY_VERSION", "v1")
    return {
        "authenticated": True,
        "consent_required": (
            False
            if is_internal_version()
            else not account_store.has_consent(owner_id, required_version)
        ),
        "privacy_version": required_version,
    }


@router.post("/api/auth/consent")
async def record_privacy_consent(
    req: PrivacyConsentRequest,
    owner_id: str = Depends(require_owner),
):
    required_version = os.getenv("WECHAT_PRIVACY_VERSION", "v1")
    if not req.agreed or req.policy_version != required_version:
        raise HTTPException(status_code=400, detail="隐私政策版本无效或未同意")
    account_store.record_consent(owner_id, required_version)
    return {"consented": True, "privacy_version": required_version}


@router.post("/api/auth/logout")
async def logout(owner_id: str = Depends(require_owner)):
    account_store.revoke_all(owner_id)
    return {"success": True}


@router.delete("/api/auth/account")
async def delete_account(owner_id: str = Depends(require_owner)):
    """Erase owned novels and durable metadata, then revoke all sessions."""

    active_jobs = job_store.list_for_owner(owner_id, active_only=True)
    if active_jobs:
        raise HTTPException(status_code=409, detail="请先取消进行中的生成或导出任务")

    novels = ownership_store.list_for_owner(owner_id)
    deleted_novels = 0
    try:
        for record in novels:
            if engine.delete_novel(record["novel_id"]):
                deleted_novels += 1
        ownership_store.purge_owner(owner_id)
        job_store.purge_owner(owner_id)
        quota_store.purge_owner(owner_id)
        export_dir = Path(NOVELS_DIR) / ".exports" / hashlib.sha256(
            owner_id.encode("utf-8")
        ).hexdigest()[:32]
        if export_dir.exists():
            shutil.rmtree(export_dir)
        account_store.delete_account(owner_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="账号数据清理失败，请稍后重试") from exc
    return {"success": True, "deleted_novels": deleted_novels}
