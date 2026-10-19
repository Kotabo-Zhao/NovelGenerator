"""Roleplay engine — SillyTavern-style chat generation (SSE events).

v2 adds:
  * slash commands (/help /say /act /nar /ooc /roll /continue /regen /swipe
    /impersonate /note /world)
  * action messages detected from natural language
  * swipe variants for assistant replies
  * lorebook (world info) activation + author's note via prompt assembly
"""
from __future__ import annotations

import asyncio
import logging
import random
import threading
import time
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional

from ..resilient_client import ResilientLLMClient
from ..llm_text import extract_text
from . import commands, worldinfo
from .card_codec import apply_macros, pick_greeting
from .prompt import build_messages
from .store import RoleplayStore

log = logging.getLogger(__name__)

_CONTINUE_DIRECTIVE = (
    "请从上一条角色回复的结尾自然继续写下去，保持同一段回复的视角、语气与场景，"
    "不要重复已经写过的内容，也不要输出角色名或解释。"
)
_SWIPE_DIRECTIVE = (
    "请重新生成最后一条角色回复，给出与上一版本不同但同样合理的发展；"
    "不要重复上一版本的句子，不要解释，直接输出角色回复。"
)
_IMPERSONATE_DIRECTIVE = (
    "你现在的任务不是扮演角色，而是替玩家（{{user}}）写下一句或一小段自然的台词/动作。"
    "只输出玩家会输入的内容，1-3 句，符合玩家的语气与当前场景；不要代替角色说话，不要解释。"
)


class RoleplayEngine:
    """Roleplay generation on top of the shared NovelEngine.

    The LLM client/model are read from the live ``NovelEngine`` on every call so
    switching providers in Settings immediately applies to roleplay chats too.
    """

    def __init__(self, llm_engine, store: RoleplayStore):
        self.llm_engine = llm_engine
        self.store = store
        # Test seam: callers may inject a fake resilient client.
        self._resilient = None

    def _resilient_client(self) -> ResilientLLMClient:
        if self._resilient is not None:
            return self._resilient
        return ResilientLLMClient(self.llm_engine.client, self.llm_engine.model)

    def active_model(self) -> str:
        return str(getattr(self.llm_engine, "model", "") or "")

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
        if self.store.chat_messages(char_id):
            self.store.archive_chat(char_id)
        greeting = self._greeting_text(card, greeting_index)
        messages = [self._new_message(
            "assistant", greeting, kind="dialogue",
            speaker=str(card.get("name") or "角色"),
        )]
        messages[0]["variants"] = [greeting]
        messages[0]["variant_index"] = 0
        self.store.save_chat(char_id, messages)
        return messages

    def swipe_existing(self, char_id: str, direction: str = "next") -> Optional[List[Dict[str, Any]]]:
        """Cycle the last assistant reply's variants without calling the model."""
        messages = self.store.chat_messages(char_id)
        if not messages:
            return None
        message = messages[-1]
        variants = message.get("variants") or []
        if message.get("role") != "assistant" or len(variants) <= 1:
            return None
        current = int(message.get("variant_index") or 0)
        step = -1 if direction == "prev" else 1
        message["variant_index"] = (current + step) % len(variants)
        message["content"] = variants[message["variant_index"]]
        self.store.save_chat(char_id, messages)
        return messages

    # --------------------------------------------------------------- generation

    def _new_message(
        self,
        role: str,
        content: str,
        *,
        kind: str = "dialogue",
        speaker: str = "",
    ) -> Dict[str, Any]:
        return {
            "id": uuid.uuid4().hex,
            "role": role,
            "kind": kind,
            "speaker": speaker,
            "content": content,
            "ts": time.time(),
        }

    def _speaker(self, card: Dict[str, Any], settings: Dict[str, Any], role: str) -> str:
        if role == "assistant":
            return str(card.get("name") or "角色")
        if role == "system":
            return "系统"
        return str(settings.get("user_name") or "旅行者")

    def _world_entries(
        self,
        char_id: str,
        settings: Dict[str, Any],
        messages: List[Dict[str, Any]],
        input_text: str,
    ) -> List[Dict[str, Any]]:
        if not settings.get("lorebook_enabled", True):
            return []
        entries = self.store.load_worldinfo(char_id)
        if not entries:
            return []
        return worldinfo.scan_world_info(
            entries,
            messages,
            input_text,
            budget=int(settings.get("lorebook_budget", 5) or 5),
            scan_depth=int(settings.get("lorebook_scan_depth", 6) or 6),
        )

    async def stream_reply(
        self,
        char_id: str,
        message: Optional[str] = None,
        regenerate: bool = False,
        request_id: Optional[str] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Yield SSE-ready events: meta / delta / message / command_result / done / error."""
        if not self.store.begin_generation(char_id):
            yield {"type": "error", "code": "busy", "message": "这个角色正在回复，请等待或停止当前生成"}
            return
        try:
            async for event in self._stream_reply_impl(char_id, message, regenerate, request_id):
                yield event
        finally:
            self.store.end_generation(char_id)

    async def _stream_reply_impl(
        self,
        char_id: str,
        message: Optional[str] = None,
        regenerate: bool = False,
        request_id: Optional[str] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        card = self.store.get_character(char_id)
        if not card:
            yield {"type": "error", "message": "角色不存在或已被删除"}
            return

        settings = self.store.settings()
        messages = self.store.chat_messages(char_id)

        request_id = request_id.strip()[:128] if isinstance(request_id, str) else ""
        if request_id and any(item.get("_request_id") == request_id for item in messages):
            yield {"type": "done", "duplicate": True}
            return

        if message is not None and str(message).strip().startswith("/"):
            command = commands.parse_command(message)
            if command is not None:
                async for event in self._handle_command(
                    char_id, card, settings, messages, command
                ):
                    yield event
                return

        if regenerate:
            last_is_assistant = bool(messages and messages[-1].get("role") == "assistant")
            history = messages[:-1] if last_is_assistant else messages
            if not any(m.get("role") == "user" for m in history):
                yield {"type": "error", "message": "还没有你的消息，先聊两句吧"}
                return
            async for event in self._generate(
                char_id, card, settings, history,
                mode="variant" if last_is_assistant else "append",
                directive=_SWIPE_DIRECTIVE,
            ):
                yield event
            return

        if message is None:
            yield {"type": "error", "message": "缺少消息内容"}
            return
        text = str(message or "").strip()
        if not text:
            yield {"type": "error", "message": "消息不能为空"}
            return
        kind = "action" if commands.looks_like_action(text) else "dialogue"
        user_message = self._new_message(
            "user", text, kind=kind, speaker=self._speaker(card, settings, "user")
        )
        if request_id:
            user_message["_request_id"] = request_id
        messages.append(user_message)
        self.store.save_chat(char_id, messages)

        async for event in self._generate(
            char_id, card, settings, messages, input_text=text, mode="append",
            pre_events=[{"type": "message", "message": user_message}],
        ):
            yield event

    async def _handle_command(
        self,
        char_id: str,
        card: Dict[str, Any],
        settings: Dict[str, Any],
        messages: List[Dict[str, Any]],
        command: commands.Command,
    ) -> AsyncIterator[Dict[str, Any]]:
        name = command.name
        args = command.args
        user_name = str(settings.get("user_name") or "旅行者")

        if not commands.is_known_command(name):
            yield {
                "type": "command_result",
                "command": name,
                "level": "error",
                "text": f"未知指令 /{name}\n{commands.HELP_TEXT}",
            }
            yield {"type": "done"}
            return

        if name == "help":
            yield {"type": "command_result", "command": "help", "text": commands.HELP_TEXT}
            yield {"type": "done"}
            return

        if name == "roll":
            try:
                roll = commands.roll_dice(args)
            except ValueError as exc:
                yield {"type": "command_result", "command": "roll", "level": "error", "text": str(exc)}
                yield {"type": "done"}
                return
            message = self._new_message("system", roll["text"], kind="roll", speaker="骰子")
            messages.append(message)
            self.store.save_chat(char_id, messages)
            yield {"type": "message", "message": message}
            yield {"type": "command_result", "command": "roll", "text": roll["text"], "data": roll}
            yield {"type": "done"}
            return

        if name == "nar":
            if not args:
                yield {"type": "command_result", "command": "nar", "level": "error", "text": "用法：/nar 旁白内容"}
                yield {"type": "done"}
                return
            message = self._new_message("assistant", args, kind="narration", speaker="旁白")
            messages.append(message)
            self.store.save_chat(char_id, messages)
            yield {"type": "message", "message": message}
            yield {"type": "command_result", "command": "nar", "text": "已插入旁白"}
            yield {"type": "done"}
            return

        if name == "note":
            updated = self.store.update_settings({"authors_note": args})
            yield {
                "type": "command_result",
                "command": "note",
                "text": "作者注释已更新" if args else "作者注释已清空",
                "data": {"authors_note": updated.get("authors_note", "")},
            }
            yield {"type": "done"}
            return

        if name == "world":
            entries = self.store.load_worldinfo(char_id)
            enabled = sum(1 for entry in entries if entry.get("enabled", True))
            yield {
                "type": "command_result",
                "command": "world",
                "text": f"世界书：共 {len(entries)} 条，已启用 {enabled} 条"
                        + ("（世界书总开关已关闭）" if not settings.get("lorebook_enabled", True) else ""),
                "data": {"total": len(entries), "enabled": enabled},
            }
            yield {"type": "done"}
            return

        if name == "impersonate":
            if not any(m.get("role") == "assistant" for m in messages):
                yield {"type": "error", "message": "先开始一段对话吧"}
                return
            history = list(messages) + [{
                "role": "user",
                "kind": "dialogue",
                "content": "（请以我的身份写下一句/段自然的台词或动作，只输出这句话，不要解释）",
            }]
            async for event in self._generate(
                char_id, card, settings, history,
                mode="impersonate", directive=_IMPERSONATE_DIRECTIVE,
            ):
                yield event
            return

        if name == "continue":
            if not any(m.get("role") == "assistant" for m in messages):
                yield {"type": "error", "message": "还没有角色回复可以继续"}
                return
            async for event in self._generate(
                char_id, card, settings, messages,
                mode="continue", directive=_CONTINUE_DIRECTIVE,
            ):
                yield event
            return

        if name == "regen":
            last_is_assistant = bool(messages and messages[-1].get("role") == "assistant")
            history = messages[:-1] if last_is_assistant else messages
            if not any(m.get("role") == "user" for m in history):
                yield {"type": "error", "message": "还没有你的消息，先聊两句吧"}
                return
            async for event in self._generate(
                char_id, card, settings, history,
                mode="variant" if last_is_assistant else "append",
                directive=_SWIPE_DIRECTIVE,
            ):
                yield event
            return

        if name == "swipe":
            direction = (args.split()[0].lower() if args else "next")
            if direction in ("next", "prev"):
                updated = self.swipe_existing(char_id, direction)
                if updated is None:
                    yield {"type": "command_result", "command": "swipe", "level": "error", "text": "没有可切换的回复版本"}
                    yield {"type": "done"}
                    return
                target = next((m for m in reversed(updated) if m.get("role") == "assistant"), None)
                if target:
                    yield {"type": "message", "message": target}
                    yield {"type": "swipe", "index": target.get("variant_index", 0), "total": len(target.get("variants") or [])}
                yield {"type": "done"}
                return
            history = messages[:-1] if messages and messages[-1].get("role") == "assistant" else messages
            if not history:
                yield {"type": "error", "message": "还没有可重写的回复"}
                return
            async for event in self._generate(
                char_id, card, settings, history,
                mode="variant" if messages and messages[-1].get("role") == "assistant" else "append",
                directive=_SWIPE_DIRECTIVE,
            ):
                yield event
            return

        if name in ("say", "act", "ooc"):
            if not args:
                yield {"type": "command_result", "command": name, "level": "error", "text": f"用法：/{name} 内容"}
                yield {"type": "done"}
                return
            kind = {"say": "dialogue", "act": "action", "ooc": "ooc"}[name]
            message = self._new_message(
                "user", args, kind=kind, speaker=self._speaker(card, settings, "user")
            )
            messages.append(message)
            self.store.save_chat(char_id, messages)
            async for event in self._generate(
                char_id, card, settings, messages, input_text=args, mode="append",
                pre_events=[{"type": "message", "message": message}],
            ):
                yield event
            return

        yield {"type": "command_result", "command": name, "level": "error", "text": "指令暂未实现"}
        yield {"type": "done"}

    async def _stream_chunks(
        self,
        api_messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[str]:
        """Real token streaming from the shared OpenAI-compatible client.

        The branch's resilient client currently collects the whole upstream
        stream in a worker thread before yielding (pseudo-streaming). Roleplay
        needs incremental delivery and a cancellable upstream, so we bridge the
        synchronous OpenAI stream through an asyncio queue here. On cancellation
        the upstream response is closed so generation stops billing.
        """
        client = getattr(self.llm_engine, "client", None)
        model = str(getattr(self.llm_engine, "model", "") or "")
        if client is None:
            raise RuntimeError("LLM 客户端未初始化")
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        stop = threading.Event()
        state: Dict[str, Any] = {"response": None}

        def put(item: Any) -> None:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, item)
            except RuntimeError:
                pass

        def worker() -> None:
            try:
                kwargs: Dict[str, Any] = {
                    "model": model,
                    "messages": api_messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "stream": True,
                    "timeout": 120,
                }
                if "v4" in model:
                    kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
                response = client.chat.completions.create(**kwargs)
                state["response"] = response
                for chunk in response:
                    if stop.is_set():
                        break
                    choices = getattr(chunk, "choices", None) or []
                    if not choices:
                        continue
                    delta = getattr(choices[0], "delta", None)
                    content = getattr(delta, "content", None)
                    text = extract_text(content)
                    if text:
                        put(text)
            except Exception as exc:  # noqa: BLE001 - forwarded to consumer
                put(exc)
            finally:
                put(None)

        threading.Thread(target=worker, daemon=True, name="roleplay-stream").start()
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            stop.set()
            response = state.get("response")
            if response is not None:
                try:
                    response.close()
                except Exception:  # noqa: BLE001 - best effort close
                    pass

    async def _generate(
        self,
        char_id: str,
        card: Dict[str, Any],
        settings: Dict[str, Any],
        history: List[Dict[str, Any]],
        *,
        input_text: str = "",
        mode: str = "append",
        directive: str = "",
        pre_events: Optional[List[Dict[str, Any]]] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        world_entries = self._world_entries(char_id, settings, history, input_text)
        api_messages = build_messages(card, settings, history, world_entries, directive)
        yield {
            "type": "meta",
            "character": str(card.get("name") or ""),
            "user": settings["user_name"],
            "model": self.active_model(),
            "world": [str(entry.get("comment") or "") for entry in world_entries],
        }
        for event in pre_events or []:
            yield event

        collected: List[str] = []
        stream = None
        try:
            if self._resilient is not None:
                stream = self._resilient.create_stream(
                    messages=api_messages,
                    temperature=float(settings["temperature"]),
                    max_tokens=int(settings["max_tokens"]),
                )
            else:
                stream = self._stream_chunks(
                    api_messages,
                    float(settings["temperature"]),
                    int(settings["max_tokens"]),
                )
            async for chunk in stream:
                text = extract_text(chunk)
                if not text:
                    continue
                collected.append(text)
                yield {"type": "delta", "content": text}
        except asyncio.CancelledError:
            self._persist_generation(char_id, card, mode, collected, partial=True)
            raise
        except GeneratorExit:
            self._persist_generation(char_id, card, mode, collected, partial=True)
            raise
        except Exception as e:  # noqa: BLE001 - surface a friendly SSE error
            log.warning("Roleplay generation failed: %s: %s", type(e).__name__, str(e)[:300])
            partial_message = self._persist_generation(
                char_id, card, mode, collected, partial=True
            )
            if partial_message is not None:
                yield {"type": "message", "message": partial_message}
            yield {
                "type": "error",
                "message": "生成失败，请稍后重试（详细原因见服务端日志）",
            }
            return
        finally:
            close = getattr(stream, "aclose", None)
            if callable(close):
                try:
                    await close()
                except (asyncio.CancelledError, GeneratorExit):
                    raise
                except Exception:
                    log.debug("Roleplay stream close failed", exc_info=True)

        if mode == "impersonate":
            text = "".join(collected).strip()
            if text:
                yield {"type": "impersonate", "text": text}
            else:
                yield {"type": "error", "message": "模型没有返回可用的台词"}
            yield {"type": "done"}
            return

        message = self._persist_generation(char_id, card, mode, collected)
        if message is not None:
            yield {"type": "message", "message": message}
            if mode == "variant":
                yield {
                    "type": "swipe",
                    "index": message.get("variant_index", 0),
                    "total": len(message.get("variants") or []),
                }
        yield {"type": "done"}

    def _persist_generation(
        self,
        char_id: str,
        card: Dict[str, Any],
        mode: str,
        collected: List[str],
        *,
        partial: bool = False,
    ) -> Optional[Dict[str, Any]]:
        text = "".join(collected).strip()
        if not text or mode == "impersonate":
            return None
        # Keep the previous assistant message intact if a regenerate/continue
        # request fails after producing only a fragment.
        if partial and mode != "append":
            return None
        messages = self.store.chat_messages(char_id)
        char_name = str(card.get("name") or "角色")
        if mode == "variant" and (
            not messages or messages[-1].get("role") != "assistant"
        ):
            mode = "append"
        if mode == "append":
            message = self._new_message("assistant", text, kind="dialogue", speaker=char_name)
            if partial:
                message["partial"] = True
            messages.append(message)
            self.store.save_chat(char_id, messages)
            return message

        target_index = None
        for index in range(len(messages) - 1, -1, -1):
            if messages[index].get("role") == "assistant":
                target_index = index
                break
        if target_index is None:
            message = self._new_message("assistant", text, kind="dialogue", speaker=char_name)
            messages.append(message)
            self.store.save_chat(char_id, messages)
            return message

        message = messages[target_index]
        variants = list(message.get("variants") or [message.get("content") or ""])
        if mode == "continue":
            index = int(message.get("variant_index") or 0)
            index = max(0, min(index, len(variants) - 1))
            variants[index] = str(variants[index]) + text
            message["variant_index"] = index
            message["content"] = variants[index]
        else:  # variant
            variants.append(text)
            message["variant_index"] = len(variants) - 1
            message["content"] = text
        message["variants"] = variants
        self.store.save_chat(char_id, messages)
        return message
