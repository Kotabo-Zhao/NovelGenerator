"""Per-generation LLM usage capture without coupling the SDK to SQLite."""

from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Iterator, Optional


_sink: ContextVar[Optional[Callable[[dict], None]]] = ContextVar("llm_usage_sink", default=None)


@contextmanager
def usage_scope(callback: Callable[[dict], None]) -> Iterator[None]:
    token = _sink.set(callback)
    try:
        yield
    finally:
        _sink.reset(token)


def report_usage(event: dict) -> None:
    callback = _sink.get()
    if callback is not None:
        callback(dict(event))


def report_response_usage(response, *, model: str) -> None:
    """Extract provider usage when present; streaming providers may omit it."""
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return

    def value(name: str, fallback: int = 0) -> int:
        raw = usage.get(name, fallback) if isinstance(usage, dict) else getattr(usage, name, fallback)
        try:
            return max(0, int(raw or 0))
        except (TypeError, ValueError):
            return fallback

    input_tokens = value("prompt_tokens", value("input_tokens"))
    output_tokens = value("completion_tokens", value("output_tokens"))
    total_tokens = value("total_tokens", input_tokens + output_tokens)
    try:
        input_rate = max(0, int(os.getenv("LLM_INPUT_COST_MICROS_PER_1K", "0") or 0))
        output_rate = max(0, int(os.getenv("LLM_OUTPUT_COST_MICROS_PER_1K", "0") or 0))
    except ValueError:
        input_rate = output_rate = 0
    cost_micros = int(input_tokens * input_rate / 1000 + output_tokens * output_rate / 1000)
    report_usage(
        {
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "cost_micros": cost_micros,
        }
    )
