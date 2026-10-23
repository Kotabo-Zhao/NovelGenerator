"""
v2.14: 统一 LLM 韧性网关

所有 LLM 调用通过此模块，提供：
- 指数退避重试（最多 3 次）
- 429 限流智能等待（读取 Retry-After header）
- 网络超时自动重试
- 流式断线重连
- 统一日志和指标收集

使用方式：
    from .resilient_client import ResilientLLMClient
    
    # 包装现有 client
    resilient = ResilientLLMClient(client, model)
    
    # 非流式调用
    result = resilient.create(messages=[...], temperature=0.8, max_tokens=4096)
    
    # 流式调用（自动重连）
    async for chunk in resilient.create_stream(messages=[...], temperature=0.8):
        yield chunk.content
"""
import time
import asyncio
import threading
import logging
from typing import AsyncIterator, Optional, Any
from dataclasses import dataclass, field
from openai import OpenAI, APIError, RateLimitError, APITimeoutError, APIConnectionError
from .llm_text import extract_text

from .usage_context import report_response_usage

log = logging.getLogger(__name__)


class LLMConfigurationError(RuntimeError):
    """Raised before a provider call when the local model credentials are unusable."""


_PLACEHOLDER_KEY_MARKERS = (
    "placeholder",
    "your-key",
    "your_api_key",
    "your-api-key",
)


def llm_error_message(error: Exception, action: str = "内容生成") -> str:
    """Return a stable user-facing error without leaking provider details."""
    if isinstance(error, LLMConfigurationError):
        return f"{action}不可用：请在设置中配置 AI API Key（或配置 DEEPSEEK_API_KEY）并重启服务器"

    status = getattr(error, "status_code", None)
    if status in (401, 403):
        return f"{action}鉴权失败：请检查设置中的 API Key（或 DEEPSEEK_API_KEY）并重启服务器"
    if status == 429:
        return f"{action}请求过于频繁，请稍后重试"
    if isinstance(error, (APITimeoutError, APIConnectionError)):
        return f"{action}暂时无法连接模型服务，请检查网络后重试"
    return f"{action}失败，请稍后重试"


@dataclass
class RetryConfig:
    """重试配置"""
    max_retries: int = 3
    base_delay: float = 1.0          # 基础延迟秒数
    max_delay: float = 30.0          # 最大延迟秒数
    backoff_multiplier: float = 2.0  # 退避乘数
    timeout: float = 120.0           # 单次调用超时


@dataclass
class LLMCallMetrics:
    """调用指标收集"""
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    retry_count: int = 0
    rate_limit_hits: int = 0
    total_tokens_used: int = 0
    total_latency_ms: float = 0.0
    
    @property
    def success_rate(self) -> float:
        return self.successful_calls / max(1, self.total_calls) * 100
    
    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / max(1, self.successful_calls)
    
    def summary(self) -> str:
        return (f"LLM指标: 调用{self.total_calls}次, "
                f"成功{self.successful_calls}次({self.success_rate:.1f}%), "
                f"重试{self.retry_count}次, "
                f"限流{self.rate_limit_hits}次, "
                f"均延迟{self.avg_latency_ms:.0f}ms")


# 全局指标实例
_metrics = LLMCallMetrics()


def get_metrics() -> LLMCallMetrics:
    """获取全局调用指标"""
    return _metrics


def reset_metrics():
    """重置指标（测试用）"""
    global _metrics
    _metrics = LLMCallMetrics()


class ResilientLLMClient:
    """统一 LLM 韧性客户端"""
    
    def __init__(self, client: OpenAI, model: str, config: RetryConfig = None):
        self.client = client
        self.model = model
        self.config = config or RetryConfig()

    def _ensure_configured(self) -> None:
        """Reject empty/test credentials before a slow provider request."""
        api_key = getattr(self.client, "api_key", None)
        if not isinstance(api_key, str):
            return
        normalized = api_key.strip().lower()
        if not normalized or any(marker in normalized for marker in _PLACEHOLDER_KEY_MARKERS):
            raise LLMConfigurationError("LLM API Key is not configured; legacy name: DEEPSEEK_API_KEY")
    
    def create(self, messages: list, temperature: float = 0.8, 
               max_tokens: int = 4096, **kwargs) -> Any:
        """非流式调用 — 带完整重试"""
        # 清理 kwargs 中可能重复的参数（原调用方可能传了这些）
        kwargs.pop("model", None)
        kwargs.pop("messages", None)
        kwargs.pop("temperature", None)
        kwargs.pop("max_tokens", None)
        kwargs.pop("stream", None)
        
        _metrics.total_calls += 1
        start = time.monotonic()

        try:
            self._ensure_configured()
        except LLMConfigurationError:
            _metrics.failed_calls += 1
            raise
        
        last_error = None
        for attempt in range(self.config.max_retries + 1):
            try:
                # 构建调用参数
                call_kwargs = dict(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=self.config.timeout,
                )
                # v4 系列自动禁用 reasoning
                if "v4" in self.model:
                    call_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
                call_kwargs.update(kwargs)
                
                response = self.client.chat.completions.create(**call_kwargs)
                report_response_usage(response, model=self.model)
                
                # 统计 token
                if hasattr(response, 'usage') and response.usage:
                    _metrics.total_tokens_used += response.usage.total_tokens
                
                elapsed = (time.monotonic() - start) * 1000
                _metrics.successful_calls += 1
                _metrics.total_latency_ms += elapsed
                
                if attempt > 0:
                    log.info(f"LLM调用成功(重试{attempt}次, {elapsed:.0f}ms)")
                
                return response
                
            except RateLimitError as e:
                _metrics.rate_limit_hits += 1
                if attempt >= self.config.max_retries:
                    _metrics.failed_calls += 1
                    raise
                wait = self._get_retry_after(e, attempt)
                log.warning(f"LLM限流(429), 等待{wait:.1f}s后重试: {e}")
                last_error = e
                _metrics.retry_count += 1
                time.sleep(wait)
                
            except (APITimeoutError, APIConnectionError) as e:
                if attempt >= self.config.max_retries:
                    _metrics.failed_calls += 1
                    raise
                wait = self._calc_backoff(attempt)
                log.warning(f"LLM网络错误, 等待{wait:.1f}s后重试: {type(e).__name__}")
                last_error = e
                _metrics.retry_count += 1
                time.sleep(wait)
                
            except APIError as e:
                status = getattr(e, "status_code", None) or 500
                # 其他 API 错误（500等）
                if attempt < self.config.max_retries and status >= 500:
                    wait = self._calc_backoff(attempt)
                    log.warning(f"LLM API错误({status}), 等待{wait:.1f}s后重试: {e}")
                    last_error = e
                    _metrics.retry_count += 1
                    time.sleep(wait)
                else:
                    log.error(f"LLM调用失败(状态码={status}, 重试={attempt}次)")
                    _metrics.failed_calls += 1
                    raise
                    
            except Exception as e:
                # 非 API 错误（参数错误等），不重试
                log.error(f"LLM调用异常(不重试): {type(e).__name__}: {e}")
                _metrics.failed_calls += 1
                raise
        
        # 所有重试都失败
        _metrics.failed_calls += 1
        log.error(f"LLM调用全部重试失败: {last_error}")
        raise last_error

    async def create_async(self, messages: list, temperature: float = 0.8,
                           max_tokens: int = 4096, **kwargs) -> Any:
        """Run the retrying synchronous API adapter off the asyncio loop."""

        return await asyncio.to_thread(
            self.create,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
    
    async def create_stream(self, messages: list, temperature: float = 0.8,
                           max_tokens: int = 4096, **kwargs) -> AsyncIterator[str]:
        """Iterate the synchronous SDK off the event loop, with bounded retries.

        Retry only before visible output. Replaying a partially delivered request
        can duplicate dialogue and charge for a different continuation.
        """
        _metrics.total_calls += 1
        start = time.monotonic()
        original_messages = list(messages)
        delivered = False
        sentinel = object()

        def read_next(iterator):
            return next(iterator, sentinel)

        for attempt in range(self.config.max_retries + 1):
            response = None
            stopped = threading.Event()
            try:
                call_kwargs = dict(
                    model=self.model,
                    messages=original_messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=True,
                    timeout=self.config.timeout,
                )
                if "v4" in self.model:
                    call_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
                call_kwargs.update(kwargs)
                def open_response():
                    opened = self.client.chat.completions.create(**call_kwargs)
                    # Cancellation can happen before the HTTP headers arrive.
                    if stopped.is_set():
                        if callable(getattr(opened, "close", None)):
                            opened.close()
                        return None
                    return opened
                response = await asyncio.to_thread(open_response)
                iterator = iter(response)
                while True:
                    chunk = await asyncio.to_thread(read_next, iterator)
                    if chunk is sentinel:
                        break
                    text = extract_text(chunk)
                    if text:
                        delivered = True
                        yield text
                if not delivered:
                    raise ValueError("模型没有返回可显示的正文，请检查模型设置后重试")
                elapsed = (time.monotonic() - start) * 1000
                _metrics.successful_calls += 1
                _metrics.total_latency_ms += elapsed
                return
            except (APITimeoutError, APIConnectionError, APIError) as exc:
                limited = isinstance(exc, RateLimitError)
                if limited:
                    _metrics.rate_limit_hits += 1
                status = getattr(exc, "status_code", None)
                retryable = limited or isinstance(exc, (APITimeoutError, APIConnectionError)) or (status and status >= 500)
                if delivered or not retryable or attempt >= self.config.max_retries:
                    _metrics.failed_calls += 1
                    raise
                _metrics.retry_count += 1
                wait = self._get_retry_after(exc, attempt) if limited else self._calc_backoff(attempt)
                log.warning("LLM stream retry %d (%s)", attempt + 1, type(exc).__name__)
                await asyncio.sleep(wait)
            except (asyncio.CancelledError, GeneratorExit):
                raise
            except Exception:
                _metrics.failed_calls += 1
                raise
            finally:
                stopped.set()
                if response is not None and callable(getattr(response, "close", None)):
                    try:
                        await asyncio.to_thread(response.close)
                    except Exception:
                        log.debug("Stream close failed", exc_info=True)
    
    def _calc_backoff(self, attempt: int) -> float:
        """计算指数退避延迟"""
        delay = self.config.base_delay * (self.config.backoff_multiplier ** attempt)
        return min(delay, self.config.max_delay)
    
    def _get_retry_after(self, error: RateLimitError, attempt: int) -> float:
        """从 429 错误中提取 Retry-After"""
        try:
            if hasattr(error, 'response') and error.response:
                retry_after = error.response.headers.get('retry-after')
                if retry_after:
                    return min(float(retry_after), self.config.max_delay)
        except (ValueError, AttributeError):
            pass
        return self._calc_backoff(attempt)
    
    def _build_resume_messages(self, original_messages: list, 
                                collected_text: str) -> list:
        """构建断线重连的 messages — 让模型从断点继续"""
        resume_msgs = list(original_messages)
        if collected_text:
            resume_msgs.append({
                "role": "assistant",
                "content": collected_text
            })
            resume_msgs.append({
                "role": "user", 
                "content": "请从上面中断的地方继续写，不要重复已写内容。"
            })
        return resume_msgs


def create_resilient_client(client: OpenAI, model: str, 
                             config: RetryConfig = None) -> ResilientLLMClient:
    """工厂函数：创建韧性客户端"""
    return ResilientLLMClient(client, model, config)
