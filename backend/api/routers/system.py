"""Local provider configuration, without returning credentials to the browser."""
from fastapi import APIRouter
from pydantic import BaseModel, Field
from ..deps import engine

router = APIRouter(prefix="/api/system", tags=["system"])


class ProviderUpdate(BaseModel):
    base_url: str = Field(max_length=500)
    model: str = Field(min_length=1, max_length=120)
    api_key: str = Field(default="", max_length=1000)
    provider: str | None = Field(default=None, max_length=80)


@router.get("/connection")
def connection_status():
    current = engine.llm_config()
    return {"configured": current["api_key_configured"], "base_url": current["base_url"],
            "model": current["model"], "provider": current["provider"]}


@router.put("/connection")
async def configure_connection(body: ProviderUpdate):
    # Compatibility with the previous connection dialog. Both UIs now share
    # the provider catalog, persistence and live NovelEngine used by roleplay.
    from ..server import set_llm_config
    current = engine.llm_config()
    base = body.base_url.strip().rstrip("/")
    provider = body.provider or (current["provider"] if base == current["base_url"].rstrip("/") else "custom")
    await set_llm_config({"provider": provider, "base_url": base,
                          "model": body.model, "api_key": body.api_key})
    return {"ok": True, **connection_status()}


@router.post("/connection/test")
async def test_connection():
    from ..server import test_llm_config
    await test_llm_config()
    return {"ok": True, "model": engine.model, "message": "模型连接测试通过"}


@router.get("/diagnostics")
def export_diagnostics():
    """Return a sanitized diagnostic snapshot (no API keys or secrets) for troubleshooting."""
    import os
    import sys as _sys
    import time as _time
    from config import NOVELS_DIR
    from core.version import APP_VERSION
    from core.diagnostics import get_recent_logs, redact_secrets

    current = engine.llm_config()
    novels_count = 0
    if os.path.isdir(NOVELS_DIR):
        novels_count = sum(
            1
            for name in os.listdir(NOVELS_DIR)
            if not name.startswith(".")
            and os.path.isdir(os.path.join(NOVELS_DIR, name))
        )
    return {
        "generated_at": int(_time.time()),
        "app_version": APP_VERSION,
        "platform": _sys.platform,
        "python_version": _sys.version.split()[0],
        "novels_count": novels_count,
        "llm": {
            "provider": current.get("provider"),
            "model": current.get("model"),
            "base_url": current.get("base_url"),
            "api_key_configured": bool(current.get("api_key_configured")),
        },
        "recent_logs": [redact_secrets(line) for line in get_recent_logs(200)],
    }
