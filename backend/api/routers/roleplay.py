"""NovelGenerator — Roleplay mode API (SillyTavern-style).

Endpoints under /api/roleplay:
  settings                 GET / PUT
  characters               GET / POST / PUT / DELETE
  characters/import        POST (SillyTavern PNG or JSON card, base64 body)
  characters/from-novel/{novel_id}  POST (convert plan.json cast into cards)
  characters/{id}/avatar   GET / POST
  characters/{id}/chat     GET
  characters/{id}/chat/start, /send (SSE), /regenerate (SSE), /clear
"""
from __future__ import annotations

import base64
import asyncio
import functools
import inspect
import binascii
import logging
import os
import time
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from ..deps import _sse_with_heartbeat, _validate_novel_id, engine
from config import NOVELS_DIR
from core.atomic_io import safe_read_json
from core.roleplay import CardError, RoleplayEngine, RoleplayStore, parse_card_bytes
from core.roleplay import novel_cards

log = logging.getLogger(__name__)
router = APIRouter()

_store = RoleplayStore()
_engine = RoleplayEngine(engine.client, engine.model, _store)

_TEXT_FIELDS = (
    "name", "description", "personality", "scenario", "first_mes", "mes_example",
    "system_prompt", "post_history_instructions", "creator", "character_version",
    "creator_notes",
)
_TEXT_LIMITS = {
    "name": 80, "description": 20000, "personality": 8000, "scenario": 8000,
    "first_mes": 8000, "mes_example": 20000, "system_prompt": 8000,
    "post_history_instructions": 8000, "creator": 120, "character_version": 60,
    "creator_notes": 4000,
}
_LIST_FIELDS = ("alternate_greetings", "tags")
_MAX_UPLOAD_BYTES = 8 * 1024 * 1024
_AVATAR_MAGIC = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"RIFF")


# --------------------------------------------------------------------- helpers

async def _json_body(request: Request) -> Dict[str, Any]:
    if int(request.headers.get("content-length") or 0) > 12 * 1024 * 1024:
        raise HTTPException(413, "文件过大（最大 8MB）")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "请求体必须是 JSON")
    if not isinstance(body, dict):
        raise HTTPException(400, "请求体必须是 JSON 对象")
    return body


def _decode_base64(value: str) -> bytes:
    cleaned = "".join(str(value or "").split())
    if cleaned.startswith("data:"):
        cleaned = cleaned.split(",", 1)[-1]
    if not cleaned:
        raise HTTPException(400, "缺少文件数据")
    if len(cleaned) > 12 * 1024 * 1024:
        raise HTTPException(413, "文件过大（最大 8MB）")
    try:
        return base64.b64decode(cleaned, validate=False)
    except (binascii.Error, ValueError) as e:
        raise HTTPException(400, f"base64 解码失败: {e}") from e


def _get_char(char_id: str) -> Dict[str, Any]:
    try:
        _store.validate_id(char_id)
    except ValueError:
        raise HTTPException(400, "无效的角色 ID") from None
    card = _store.get_character(char_id)
    if not card:
        raise HTTPException(404, "角色不存在")
    return card


def _ensure_idle(char_id: str):
    if _store.is_generating(char_id):
        raise HTTPException(409, "该角色正在回复，请先停止生成")


def exclusive_character(function):
    """Reserve before awaiting request data; no clear/delete versus send race."""
    @functools.wraps(function)
    async def guarded(char_id: str, *args, **kwargs):
        _get_char(char_id)
        if not _store.begin_mutation(char_id):
            raise HTTPException(409, "该角色正在处理其他操作，请稍后重试")
        try:
            if inspect.iscoroutinefunction(function):
                return await function(char_id, *args, **kwargs)
            return await asyncio.to_thread(function, char_id, *args, **kwargs)
        finally:
            _store.end_mutation(char_id)
    return guarded


def _public(card: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(card)
    cid = str(out.get("id") or "")
    if cid and _store.has_avatar(cid):
        out["avatar_url"] = (
            f"/api/roleplay/characters/{cid}/avatar?v={int((out.get('updated_at') or 0) * 1000)}"
        )
    else:
        out["avatar_url"] = ""
    return out


def _apply_card_fields(body: Dict[str, Any], base: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    card = dict(base or {})
    for field in _TEXT_FIELDS:
        if field in body:
            value = body.get(field)
            if value is None:
                value = ""
            if not isinstance(value, str):
                raise HTTPException(400, f"{field} 必须是字符串")
            card[field] = value.strip()[: _TEXT_LIMITS.get(field, 20000)]
    for field in _LIST_FIELDS:
        if field in body:
            value = body.get(field)
            if isinstance(value, str):
                value = [value]
            if not isinstance(value, list):
                raise HTTPException(400, f"{field} 必须是字符串数组")
            card[field] = [str(item).strip() for item in value if str(item).strip()][:50]
    return card


# -------------------------------------------------------------------- settings

@router.get("/api/roleplay/settings")
def get_settings():
    return {"ok": True, "settings": _store.settings()}


@router.put("/api/roleplay/settings")
async def update_settings(request: Request):
    body = await _json_body(request)
    settings = _store.update_settings(body)
    return {"ok": True, "settings": settings}


# ------------------------------------------------------------------ characters

@router.get("/api/roleplay/characters")
def list_characters():
    return {"ok": True, "characters": [_public(c) for c in _store.list_characters()]}


@router.get("/api/roleplay/characters/{char_id}")
def get_character(char_id: str):
    return {"ok": True, "character": _public(_get_char(char_id))}


@router.post("/api/roleplay/characters")
async def create_character(request: Request):
    body = await _json_body(request)
    card = _apply_card_fields(body)
    if not str(card.get("name") or "").strip():
        raise HTTPException(400, "角色名不能为空")
    now = time.time()
    card["id"] = uuid.uuid4().hex
    card["source"] = "manual"
    card["created_at"] = now
    card["updated_at"] = now
    _store.upsert_character(card)
    return {"ok": True, "character": _public(card)}


@router.put("/api/roleplay/characters/{char_id}")
@exclusive_character
async def update_character(char_id: str, request: Request):
    existing = _get_char(char_id)
    _ensure_idle(char_id)
    body = await _json_body(request)
    card = _apply_card_fields(body, existing)
    if not str(card.get("name") or "").strip():
        raise HTTPException(400, "角色名不能为空")
    _store.upsert_character(card)
    return {"ok": True, "character": _public(card)}


@router.delete("/api/roleplay/characters/{char_id}")
@exclusive_character
async def delete_character(char_id: str):
    _get_char(char_id)
    _ensure_idle(char_id)
    if not _store.delete_character(char_id):
        raise HTTPException(404, "角色不存在")
    return {"ok": True}


@router.post("/api/roleplay/characters/import")
async def import_character(request: Request):
    body = await _json_body(request)
    data = _decode_base64(str(body.get("data_base64") or ""))
    if not data:
        raise HTTPException(400, "文件为空")
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(413, "角色卡文件过大（最大 8MB）")
    try:
        card, fmt = parse_card_bytes(data, str(body.get("filename") or ""))
    except CardError as e:
        raise HTTPException(400, str(e)) from e

    now = time.time()
    card["id"] = uuid.uuid4().hex
    card["source"] = fmt
    card["created_at"] = now
    card["updated_at"] = now
    _store.upsert_character(card)
    if fmt == "png":
        _store.save_avatar(card["id"], data)
    log.info("Imported roleplay card: %s (%s)", card["name"], fmt)
    return {"ok": True, "character": _public(card), "format": fmt}


@router.post("/api/roleplay/characters/from-novel/{novel_id}")
async def characters_from_novel(novel_id: str):
    _validate_novel_id(novel_id)
    novel_dir = os.path.join(NOVELS_DIR, novel_id)
    if not os.path.isdir(novel_dir):
        raise HTTPException(404, "小说不存在")
    plan = safe_read_json(os.path.join(novel_dir, "plan.json"), default=None)
    if not isinstance(plan, dict) or not plan:
        raise HTTPException(404, "该小说还没有生成设定（plan.json）")

    cards = novel_cards.build_cards_from_plan(plan, novel_id)
    if not cards:
        raise HTTPException(404, "该小说的角色宝典里还没有可用角色")

    existing = _store.list_characters()
    existing_names = {
        str(c.get("name")) for c in existing
        if c.get("source") == "novel" and c.get("novel_id") == novel_id
    }
    created = []
    skipped = 0
    for card in cards:
        if str(card.get("name")) in existing_names:
            skipped += 1
            continue
        card["id"] = uuid.uuid4().hex
        now = time.time()
        card["created_at"] = now
        card["updated_at"] = now
        _store.upsert_character(card)
        created.append(_public(card))
        existing_names.add(str(card.get("name")))
    return {"ok": True, "characters": created, "created": len(created), "skipped": skipped}


# --------------------------------------------------------------------- avatars

@router.get("/api/roleplay/characters/{char_id}/avatar")
def get_avatar(char_id: str):
    _get_char(char_id)
    path = _store.avatar_path(char_id)
    if not os.path.isfile(path):
        raise HTTPException(404, "该角色没有头像")
    with open(path, "rb") as f:
        data = f.read()
    return Response(
        content=data,
        media_type=_store.avatar_media_type(char_id),
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.post("/api/roleplay/characters/{char_id}/avatar")
@exclusive_character
async def upload_avatar(char_id: str, request: Request):
    _get_char(char_id)
    _ensure_idle(char_id)
    body = await _json_body(request)
    data = _decode_base64(str(body.get("data_base64") or ""))
    if not data:
        raise HTTPException(400, "头像文件为空")
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(413, "头像文件过大（最大 8MB）")
    if not any(data.startswith(magic) for magic in _AVATAR_MAGIC):
        raise HTTPException(400, "头像只支持 PNG / JPEG / WebP")
    _store.save_avatar(char_id, data)
    card = _get_char(char_id)
    card = _store.upsert_character(card)
    return {"ok": True, "avatar_url": _public(card)["avatar_url"]}


# ----------------------------------------------------------------------- chats

@router.get("/api/roleplay/characters/{char_id}/chat")
def get_chat(char_id: str):
    _get_char(char_id)
    return {"ok": True, "messages": _store.chat_messages(char_id), "generating": _store.is_generating(char_id)}


@router.post("/api/roleplay/characters/{char_id}/chat/start")
@exclusive_character
async def start_chat(char_id: str):
    _get_char(char_id)
    _ensure_idle(char_id)
    messages = _engine.ensure_chat(char_id)
    return {"ok": True, "messages": messages}


@router.post("/api/roleplay/characters/{char_id}/chat/send")
async def send_chat(char_id: str, request: Request):
    _get_char(char_id)
    _ensure_idle(char_id)
    body = await _json_body(request)
    message = body.get("message")
    if not isinstance(message, str):
        raise HTTPException(400, "消息必须是文本")
    message = message.strip()
    if not message:
        raise HTTPException(400, "消息不能为空")
    if len(message) > 8000:
        raise HTTPException(400, "消息过长（最多 8000 字）")
    request_id = body.get("request_id")
    if request_id is not None and (not isinstance(request_id, str) or len(request_id) > 96):
        raise HTTPException(400, "无效的请求 ID")
    return _sse_response(_engine.stream_reply(char_id, message=message, request_id=request_id))


@router.post("/api/roleplay/characters/{char_id}/chat/regenerate")
async def regenerate_chat(char_id: str):
    _get_char(char_id)
    _ensure_idle(char_id)
    if not any(m["role"] == "user" for m in _store.chat_messages(char_id)):
        raise HTTPException(400, "请先发送一条消息")
    return _sse_response(_engine.stream_reply(char_id, regenerate=True))


@router.post("/api/roleplay/characters/{char_id}/chat/clear")
@exclusive_character
async def clear_chat(char_id: str, request: Request):
    _get_char(char_id)
    _ensure_idle(char_id)
    greeting_index: Optional[int] = None
    try:
        body = await request.json()
        if isinstance(body, dict) and body.get("greeting_index") is not None:
            greeting_index = int(body["greeting_index"])
    except Exception:
        pass
    messages = _engine.reset_chat(char_id, greeting_index)
    return {"ok": True, "messages": messages}


@router.patch("/api/roleplay/characters/{char_id}/chat/messages/{message_id}")
@exclusive_character
async def edit_message(char_id: str, message_id: str, request: Request):
    _get_char(char_id)
    _ensure_idle(char_id)
    body = await _json_body(request)
    content = body.get("content")
    if not isinstance(content, str) or not content.strip() or len(content) > 50000:
        raise HTTPException(400, "消息内容须为 1–50000 字的文本")
    messages = _store.chat_messages(char_id)
    target = next((m for m in messages if m["id"] == message_id), None)
    if target is None:
        raise HTTPException(404, "消息不存在")
    _store.archive_chat(char_id)
    target.update(content=content.strip(), edited_at=time.time())
    _store.save_chat(char_id, messages)
    return {"ok": True, "message": target}


@router.post("/api/roleplay/characters/{char_id}/chat/restore")
@exclusive_character
def restore_chat(char_id: str):
    _get_char(char_id)
    _ensure_idle(char_id)
    folder = os.path.join(_store.root, "history", char_id)
    names = sorted(os.listdir(folder)) if os.path.isdir(folder) else []
    if not names:
        raise HTTPException(404, "还没有可恢复的历史记录")
    path = os.path.join(folder, names[-1])
    snapshot = safe_read_json(path)
    messages = snapshot.get("messages") if isinstance(snapshot, dict) else None
    if not isinstance(messages, list):
        raise HTTPException(400, "历史记录无法读取")
    # Swap with the snapshot so this action itself is reversible.
    current = _store.chat_messages(char_id)
    _store.save_chat(char_id, messages)
    from core.atomic_io import atomic_write_json
    atomic_write_json(path, {"messages": current, "character_id": char_id})
    return {"ok": True, "messages": _store.chat_messages(char_id)}


def _sse_response(event_generator) -> StreamingResponse:
    return StreamingResponse(
        _sse_with_heartbeat(event_generator),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
