"""统一的 LLM 供应商预设与 OpenAI-compatible 客户端构造。

大多数供应商都能通过 OpenAI Chat Completions 接口接入，但认证头、
Base URL 和模型 ID 并不完全相同。把这些差异集中在这里，避免业务层
到处写死 DeepSeek 配置，也让前端可以直接消费同一份供应商目录。
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any
from urllib.parse import urlparse


_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_PLACEHOLDER_MARKERS = (
    "your_",
    "your-",
    "your key",
    "your-key",
    "placeholder",
    "sk-placeholder",
)


@dataclass(frozen=True)
class ProviderPreset:
    """可直接展示给用户的供应商配置模板。"""

    id: str
    name: str
    description: str
    base_url: str
    models: tuple[str, ...]
    default_model: str
    key_placeholder: str = "请输入 API Key"
    auth_header: str = "authorization"
    docs_url: str = ""
    custom_model: bool = True

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "base_url": self.base_url,
            "models": list(self.models),
            "default_model": self.default_model,
            "key_placeholder": self.key_placeholder,
            "auth_header": self.auth_header,
            "docs_url": self.docs_url,
            "custom_model": self.custom_model,
        }


# 模型 ID 是“默认建议”，不是硬编码白名单。供应商会持续发布新模型，
# 因此后端允许用户填写合法的自定义模型 ID；预设只负责一键带入常用值。
PROVIDER_PRESETS: tuple[ProviderPreset, ...] = (
    ProviderPreset(
        id="deepseek",
        name="DeepSeek",
        description="中文创作性价比高，适合长篇规划与正文生成",
        base_url="https://api.deepseek.com",
        models=("deepseek-v4-flash", "deepseek-v4-pro", "deepseek-chat"),
        default_model="deepseek-v4-flash",
        key_placeholder="sk-...",
        docs_url="https://api-docs.deepseek.com/",
    ),
    ProviderPreset(
        id="dots",
        name="小红书 Dots",
        description="Dots Studio 模型，兼容 Chat Completions，使用 api-key 认证",
        base_url="https://note3-prev-api.askdiandian.com/v1",
        models=("dots3-note-prev",),
        default_model="dots3-note-prev",
        key_placeholder="Dots API Key",
        auth_header="api-key",
        docs_url="https://dots.ai/platform/docs",
    ),
    ProviderPreset(
        id="openai",
        name="OpenAI",
        description="GPT 系列官方接口，适合高质量改稿与审校",
        base_url="https://api.openai.com/v1",
        models=("gpt-5.2", "gpt-5-mini"),
        default_model="gpt-5.2",
        key_placeholder="sk-...",
        docs_url="https://platform.openai.com/docs/",
    ),
    ProviderPreset(
        id="qwen",
        name="通义千问",
        description="阿里云百炼兼容接口，适合中文大纲和批量生成",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        models=("qwen-plus", "qwen-max", "qwen-turbo"),
        default_model="qwen-plus",
        key_placeholder="sk-...",
        docs_url="https://help.aliyun.com/zh/model-studio/",
    ),
    ProviderPreset(
        id="moonshot",
        name="Kimi / Moonshot",
        description="长上下文中文模型，适合大纲、设定和连续性审阅",
        base_url="https://api.moonshot.cn/v1",
        models=("kimi-k2.5", "moonshot-v1-128k", "moonshot-v1-32k"),
        default_model="kimi-k2.5",
        key_placeholder="sk-...",
        docs_url="https://platform.moonshot.cn/docs/",
    ),
    ProviderPreset(
        id="zhipu",
        name="智谱 GLM",
        description="国产通用模型，适合中文创作与结构化输出",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        models=("glm-4.5", "glm-4-flash", "glm-4-plus"),
        default_model="glm-4.5",
        key_placeholder="API Key",
        docs_url="https://open.bigmodel.cn/dev/api",
    ),
    ProviderPreset(
        id="doubao",
        name="豆包 / 火山方舟",
        description="火山方舟兼容接口；模型也可填写控制台生成的 ep- 接入点",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        models=("doubao-seed-2-0-pro-260215", "doubao-seed-2-0-lite-260215"),
        default_model="doubao-seed-2-0-pro-260215",
        key_placeholder="Ark API Key",
        docs_url="https://www.volcengine.com/docs/82379/",
    ),
    ProviderPreset(
        id="siliconflow",
        name="硅基流动",
        description="多模型聚合接口，可按需切换 DeepSeek、Qwen 等模型",
        base_url="https://api.siliconflow.cn/v1",
        models=("deepseek-ai/DeepSeek-V3", "Qwen/Qwen3-235B-A22B"),
        default_model="deepseek-ai/DeepSeek-V3",
        key_placeholder="sk-...",
        docs_url="https://docs.siliconflow.cn/",
    ),
    ProviderPreset(
        id="custom",
        name="自定义兼容接口",
        description="适配本地代理、企业网关或其他 OpenAI-compatible 服务",
        base_url="http://127.0.0.1:8001/v1",
        models=(),
        default_model="",
        key_placeholder="可留空或填写 API Key",
        docs_url="",
    ),
)

_PRESETS = {item.id: item for item in PROVIDER_PRESETS}


def get_provider(provider_id: str | None) -> ProviderPreset:
    provider_id = (provider_id or "deepseek").strip().lower()
    try:
        return _PRESETS[provider_id]
    except KeyError as exc:
        raise ValueError("不支持的 LLM 供应商") from exc


def list_providers() -> list[dict[str, Any]]:
    """返回不含任何密钥的公开目录。"""
    return [item.public_dict() for item in PROVIDER_PRESETS]


def is_configured_key(api_key: str | None) -> bool:
    value = (api_key or "").strip()
    if not value:
        return False
    lowered = value.lower()
    return not any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


def mask_api_key(api_key: str | None) -> str:
    value = (api_key or "").strip()
    if not is_configured_key(value):
        return ""
    return f"••••{value[-4:]}" if len(value) >= 4 else "••••"


def validate_api_key(api_key: str | None, *, required: bool = True) -> str:
    value = (api_key or "").strip()
    if not value and not required:
        return ""
    if len(value) < 8 or len(value) > 512:
        raise ValueError("API Key 长度无效")
    if any(ord(char) < 32 or char.isspace() for char in value):
        raise ValueError("API Key 不得包含空格或控制字符")
    if not is_configured_key(value):
        raise ValueError("API Key 不能使用占位符")
    return value


def validate_model(model: str | None, *, required: bool = True) -> str:
    value = (model or "").strip()
    if not value and not required:
        return ""
    if not _MODEL_RE.fullmatch(value):
        raise ValueError("模型 ID 格式无效")
    return value


def validate_base_url(base_url: str | None, *, default: str = "") -> str:
    value = (base_url or default or "").strip().rstrip("/")
    if len(value) > 512:
        raise ValueError("Base URL 过长")
    parsed = urlparse(value)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError("Base URL 必须是 http(s) 地址")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Base URL 不得包含账号、密码、查询参数或片段")
    if parsed.scheme == "http" and parsed.hostname not in {
        "localhost", "127.0.0.1", "::1"
    }:
        raise ValueError("非本机接口必须使用 HTTPS")
    return value


def resolve_config(
    provider: str | None,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    current_api_key: str | None = None,
) -> dict[str, str]:
    """校验并补齐一份可直接应用的配置。"""
    preset = get_provider(provider)
    key = validate_api_key(api_key, required=False)
    if not key:
        key = (current_api_key or "").strip()
    base = validate_base_url(base_url, default=preset.base_url)
    # 即使是自定义供应商也必须明确模型 ID；否则连接测试和生成请求只能
    # 在远端返回含糊的 400，用户很难知道错在哪里。
    selected_model = validate_model(model or preset.default_model, required=True)
    return {
        "provider": preset.id,
        "api_key": key,
        "base_url": base,
        "model": selected_model,
    }


def build_client(api_key: str | None, base_url: str, provider: str):
    """构造共享 OpenAI 客户端，供应商差异只在这里落地。"""
    from openai import OpenAI

    preset = get_provider(provider)
    client_kwargs: dict[str, Any] = {
        "api_key": api_key or "sk-placeholder-for-tests",
        "base_url": base_url,
    }
    if preset.auth_header != "authorization" and api_key:
        client_kwargs["default_headers"] = {preset.auth_header: api_key}
    return OpenAI(**client_kwargs)
