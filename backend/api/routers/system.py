"""Local provider configuration, without returning credentials to the browser."""
import asyncio
import os
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from openai import OpenAI

import config
from core.atomic_io import atomic_write_json
from core.resilient_client import ResilientLLMClient
from ..deps import engine
from ..middleware import active_generations

router = APIRouter(prefix="/api/system", tags=["system"])


class ProviderUpdate(BaseModel):
    base_url: str = Field(max_length=500)
    model: str = Field(min_length=1, max_length=120)
    api_key: str = Field(default="", max_length=1000)


@router.get("/connection")
def connection_status():
    return {"configured": bool(config.DEEPSEEK_API_KEY), "base_url": config.DEEPSEEK_BASE_URL,
            "model": config.DEEPSEEK_MODEL}


@router.put("/connection")
def configure_connection(body: ProviderUpdate):
    from . import roleplay
    if active_generations() or roleplay._store._generating:
        raise HTTPException(409, "请先停止当前生成，再修改模型连接")
    base = body.base_url.strip().rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HTTPException(400, "请输入有效的 HTTP / HTTPS API 地址")
    key = body.api_key.strip() or config.DEEPSEEK_API_KEY
    model = body.model.strip()
    if not model:
        raise HTTPException(400, "模型名称不能为空")
    if any(c in key + model for c in "\r\n\x00"):
        raise HTTPException(400, "连接设置包含无效字符")
    client = OpenAI(api_key=key or "local-no-key", base_url=base, max_retries=0)
    payload = {"api_key": key, "base_url": base, "model": model}
    atomic_write_json(config.PROVIDER_FILE, payload)
    # Core agents share one provider. Update the graph without recreating memory
    # managers or touching novels. Requests already streaming retain their client.
    old_client = engine.client
    visited = set()
    pending = [engine]
    while pending:
        obj = pending.pop()
        if id(obj) in visited:
            continue
        visited.add(id(obj))
        for name, value in list(vars(obj).items()):
            if value is old_client:
                setattr(obj, name, client)
            elif type(value).__module__.startswith(("core.", "backend.core.")) and hasattr(value, "__dict__"):
                pending.append(value)
        if hasattr(obj, "model"):
            obj.model = model
    roleplay._engine.client = client
    roleplay._engine.model = model
    roleplay._engine._resilient = ResilientLLMClient(client, model)
    config.DEEPSEEK_API_KEY, config.DEEPSEEK_BASE_URL, config.DEEPSEEK_MODEL = key, base, model
    return {"ok": True, **connection_status()}


@router.post("/connection/test")
async def test_connection():
    """A small real generation probe, only when explicitly requested by the UI."""
    try:
        kwargs = {"model": engine.model, "messages": [{"role": "user", "content": "Reply OK."}],
                  "max_tokens": 32, "timeout": 20}
        if "v4" in engine.model:
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        response = await asyncio.to_thread(engine.client.chat.completions.create, **kwargs)
        if not response.choices or not response.choices[0].message.content:
            raise HTTPException(502, "模型连接成功，但没有返回正文")
        return {"ok": True, "model": engine.model, "message": "模型已成功返回正文"}
    except HTTPException:
        raise
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        detail = {401: "API Key 无效，请检查后重试", 403: "账号没有此模型的访问权限",
                  404: "API 地址或模型名称不存在", 429: "模型服务限流或账号额度不足"}.get(status)
        raise HTTPException(502, detail or "连接失败，请检查 API 地址、代理和网络") from None
