"""Roleplay engine — SillyTavern-style chat generation (SSE events)."""
from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional

from ..resilient_client import ResilientLLMClient
from ..llm_text import display_text, extract_text
from .card_codec import apply_macros, pick_greeting
from .prompt import build_messages
from .store import RoleplayStore

log = logging.getLogger(__name__)


class RoleplayEngine:
    def __init__(self, client, model: str, store: RoleplayStore):
        self.client = client
        self.model = model
        self.store = store
        self._resilient = ResilientLLMClient(client, model)

    # ------------------------------------------------------------------ chats

    def _greeting_text(self, card: Dict[str, Any], greeting_index: Optional[int] = None) -> str:
        settings = self.store.settings()
        raw: str
        if greeting_index is None:
            greetings: List[str] = []
            first = str(card.get("first_mes") or "").strip()
            if first:
                greetings.append(first)
            greetings.extend(
                str(item).strip()
                for item in (card.get("alternate_greetings") or [])
                if str(item).strip()
            )
            # Contract: no explicit selection -> random greeting ("清空/重开" 随机开场).
            raw = random.choice(greetings) if greetings else pick_greeting(card, None)
        else:
            raw = pick_greeting(card, greeting_index)
        return apply_macros(raw, str(card.get("name") or "角色"), settings["user_name"])

    def ensure_chat(self, char_id: str, greeting_index: Optional[int] = None) -> List[Dict[str, Any]]:
        messages = self.store.chat_messages(char_id)
        if messages:
            return messages
        return self.reset_chat(char_id, greeting_index)

    def reset_chat(self, char_id: str, greeting_index: Optional[int] = None) -> List[Dict[str, Any]]:
        card = self.store.get_character(char_id)
        if not card:
            return []
        messages = [{
            "id": uuid.uuid4().hex,
            "role": "assistant",
            "content": self._greeting_text(card, greeting_index),
            "ts": time.time(),
        }]
        self.store.archive_chat(char_id)
        self.store.save_chat(char_id, messages)
        return messages

    # -------------------------------------------------------------- generation

    async def stream_reply(
        self,
        char_id: str,
        message: Optional[str] = None,
        regenerate: bool = False,
        request_id: Optional[str] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """One writer per character; durable partials and non-destructive retries."""
        if not self.store.begin_generation(char_id):
            yield {"type": "error", "code": "busy", "message": "这个角色正在回复，请等待或停止当前生成"}
            return
        collected: List[str] = []
        messages = []
        reply = None
        complete = False
        failure = None
        try:
            card = await asyncio.to_thread(self.store.get_character, char_id)
            if not card:
                yield {"type": "error", "message": "角色不存在或已被删除"}
                return
            settings = await asyncio.to_thread(self.store.settings)
            messages = await asyncio.to_thread(self.ensure_chat, char_id)
            if regenerate:
                if not any(m.get("role") == "user" for m in messages):
                    yield {"type": "error", "message": "还没有你的消息，先聊两句吧"}
                    return
                await asyncio.to_thread(self.store.archive_chat, char_id)
                while messages and messages[-1]["role"] == "assistant":
                    messages.pop()
            elif isinstance(message, str) and message.strip():
                duplicate = next((m for m in messages if request_id and m.get("request_id") == request_id), None)
                if duplicate:
                    existing = next((m for m in messages if m.get("reply_to") == duplicate["id"]), None)
                    if existing:
                        yield {"type": "done", "message": existing, "duplicate": True}
                        return
                    if messages[-1]["id"] != duplicate["id"]:
                        yield {"type": "error", "message": "这条消息已保留在历史记录中，请刷新对话"}
                        return
                else:
                    messages.append({"id": uuid.uuid4().hex, "role": "user", "content": message.strip(),
                                     "ts": time.time(), "request_id": request_id})
                    await asyncio.to_thread(self.store.save_chat, char_id, messages)
            else:
                yield {"type": "error", "message": "消息不能为空"}
                return
            reply = {"id": uuid.uuid4().hex, "role": "assistant", "content": "", "ts": time.time(),
                     "reply_to": messages[-1]["id"], "partial": True}
            yield {"type": "meta", "character": card["name"], "user": settings["user_name"],
                   "model": self.model, "message_id": reply["id"], "user_message_id": messages[-1]["id"], "request_id": request_id}
            checkpoint = time.monotonic()
            async for chunk in self._resilient.create_stream(
                messages=build_messages(card, settings, messages),
                temperature=float(settings["temperature"]),
                max_tokens=int(settings["max_tokens"]),
            ):
                text = extract_text(chunk)
                if not text:
                    continue
                collected.append(text)
                yield {"type": "delta", "content": text}
                if time.monotonic() - checkpoint >= 2:
                    reply["content"] = "".join(collected)
                    await asyncio.to_thread(self.store.save_chat, char_id, messages + [dict(reply)])
                    checkpoint = time.monotonic()
            complete = bool(collected)
            if not complete:
                failure = "模型没有返回可显示的正文，请检查连接设置后重试"
        except (asyncio.CancelledError, GeneratorExit):
            raise
        except Exception as exc:
            log.warning("Roleplay generation failed (%s)", type(exc).__name__)
            status = getattr(exc, "status_code", None)
            failure = ({401: "模型鉴权失败，请检查连接设置中的 API Key", 403: "当前账号没有这个模型的访问权限",
                        429: "模型服务繁忙或额度不足，请稍后重试"}.get(status)
                       or "生成中断，已保留你的消息和已收到的正文，可以重新生成")
        finally:
            try:
                if reply is not None and collected:
                    reply["content"] = display_text("".join(collected)).strip()
                    reply["partial"] = not complete
                    # Also runs on disconnect/async-generator close; no append race.
                    await asyncio.shield(asyncio.to_thread(self.store.save_chat, char_id, messages + [reply]))
            finally:
                self.store.end_generation(char_id)
        if failure:
            yield {"type": "error", "message": failure}
        else:
            yield {"type": "done", "message": reply}
