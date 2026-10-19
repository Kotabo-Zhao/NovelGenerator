"""Minimal OpenAI-compatible mock server for local roleplay verification.

Run:
    .venv/Scripts/python.exe backend/tests/mock_llm_server.py
"""
from __future__ import annotations

import asyncio
import json
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI(title="mock-openai")

REPLY = '*她抬起头，把茶杯推到你面前。*\n\n「你好，旅行者。雨还在下。🌙」'


def _last_user_text(body: dict) -> str:
    for message in reversed(body.get("messages") or []):
        if isinstance(message, dict) and message.get("role") == "user":
            return str(message.get("content") or "")
    return ""


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    model = body.get("model") or "mock-model"
    user_text = _last_user_text(body)
    if "[auth-error]" in user_text:
        return JSONResponse({"error": {"message": "test authentication failure", "type": "authentication_error"}}, status_code=401)

    if body.get("stream"):
        async def event_stream():
            parts = list(REPLY)
            if "[slow]" in user_text:
                parts = list("*她翻开了一本旧书。*\n\n" + "窗外的雨声还没有停。" * 30)
            for part in parts:
                chunk = {
                    "id": "chatcmpl-mock",
                    "object": "chat.completion.chunk",
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "delta": {"content": part},
                        "finish_reason": None,
                    }],
                }
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                await asyncio.sleep(0.03 if "[slow]" in user_text else 0.003)
            final = {
                "id": "chatcmpl-mock",
                "object": "chat.completion.chunk",
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            yield f"data: {json.dumps(final, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    return JSONResponse({
        "id": "chatcmpl-mock",
        "object": "chat.completion",
        "model": model,
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": f"{REPLY}（收到：{user_text[:40]}）",
            },
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    })


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [{"id": "mock-model", "object": "model"}]}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.getenv("MOCK_PORT", "8124")),
        log_level="error",
    )
