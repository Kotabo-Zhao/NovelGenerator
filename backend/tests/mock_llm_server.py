"""Minimal OpenAI-compatible mock server for local roleplay verification.

Run:
    .venv/Scripts/python.exe backend/tests/mock_llm_server.py
"""
from __future__ import annotations

import asyncio
import json
import os
import re

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI(title="mock-openai")

REPLY = "你好，我是 mock 模型，正在扮演角色。"


def _last_user_text(body: dict) -> str:
    for message in reversed(body.get("messages") or []):
        if isinstance(message, dict) and message.get("role") == "user":
            return str(message.get("content") or "")
    return ""


def _system_text(body: dict) -> str:
    return "\n".join(
        str(message.get("content") or "")
        for message in (body.get("messages") or [])
        if isinstance(message, dict) and message.get("role") == "system"
    )


_SCENE_MARKUP = (
    "【旁白】夜雨敲窗，客栈里只剩几盏将熄的灯。你推门而入，潮湿的水汽跟着涌进来，"
    "柜台后的算盘停在半截，楼上的脚步声也忽然轻了。\n"
    "【顾尘】「坐吧。」*他抬眼看你，手仍按在剑柄上，只把对面的长凳往你这边踢了踢。*\n"
    "【苏晚】「先喝口热茶。」*她把粗陶杯推到你面前，目光在你和顾尘之间转了一圈，*"
    "「外面雨这么大，今晚怕是走不了了。」"
)
_CHAT_REPLY = "「你终于来了。」*顾尘把剑往桌边挪了挪，给你让出半边位置，*「坐。」"


def _interactive_text(body: dict) -> str:
    """Mock the interactive director/chat prompts so local E2E tests work."""
    system = _system_text(body)
    if "互动小说导演" in system:
        return _SCENE_MARKUP
    if "小说角色扮演引擎" in system:
        return _CHAT_REPLY
    return ""


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    model = body.get("model") or "mock-model"
    user_text = _last_user_text(body)
    interactive_text = _interactive_text(body)

    # AI roleplay card writing: return one SillyTavern card per requested role.
    if "## 输出" in user_text and "角色卡" in user_text:
        names = [name.strip() for name in re.findall(
            r"^###\s*\d+\.\s*([^\n（(]+)", user_text, re.MULTILINE
        ) if name.strip()]
        if not names:
            names = ["顾尘"]
        cards = []
        for name in names:
            cards.append({
                "name": name,
                "description": f"AI 根据小说设定为{name}生成的外貌、身份与背景：他/她带着未说出口的过去，站在故事开始的地方。",
                "personality": "冷静、克制、重情；说话简短，习惯用行动代替解释。",
                "scenario": f"深夜的客栈，灯火将熄。{{{{user}}}}推门而入，{name}正坐在窗边。",
                "first_mes": f"*{name}抬眼看向你，手指在杯沿停了一下。*「坐吧。这个时辰还出门的人，多半有心事。」",
                "mes_example": (
                    "<START>\n{{user}}: 你是谁？\n"
                    f"{{{{char}}}}: 一个路过的人。*{name}把杯子往你那边推了推。*"
                ),
                "system_prompt": f"你是小说中的{name}。请始终以角色身份回应，不要跳出角色。",
                "post_history_instructions": "保持人物性格与说话方式，不要 OOC。",
                "alternate_greetings": [f"*{name}没有回头。*「门没锁，自己进来。」"],
                "tags": ["AI写卡", "主角", "测试"],
            })
        payload = json.dumps({"cards": cards}, ensure_ascii=False)
        return JSONResponse({
            "id": "chatcmpl-mock",
            "object": "chat.completion",
            "model": model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": payload},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    if body.get("stream"):
        async def event_stream():
            parts = list(interactive_text) if interactive_text else ["你好", "，", "我是 ", "mock 模型", "。"]
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
                await asyncio.sleep(0.01)
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
                "content": interactive_text or f"{REPLY}（收到：{user_text[:40]}）",
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
