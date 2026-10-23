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
import binascii
import json
import logging
import os
import shutil
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from ..auth import is_internal_version, require_owner
from ..deps import _sse_with_heartbeat, _validate_novel_id, engine, ownership_store
from config import NOVELS_DIR
from core.atomic_io import safe_read_json
from core.roleplay import CardError, RoleplayEngine, RoleplayStore, parse_card_bytes
from core.roleplay import novel_cards, worldinfo
from core.roleplay.identity import character_to_persona
from core.roleplay.identity import role_mode
from core.roleplay.compatibility import card_features
from core.roleplay.regex_rules import display_messages
from core.roleplay.marketplace import Marketplace, MarketError

log = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(require_owner)])

_store = RoleplayStore()
_engine = RoleplayEngine(engine, _store)
_market = Marketplace()

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
    try:
        raw = await request.body()
    except Exception as exc:
        raise HTTPException(400, "无法读取请求体") from exc
    if len(raw) > 12 * 1024 * 1024:
        raise HTTPException(413, "请求体过大（最大 12MB）")
    try:
        body = json.loads(raw)
    except Exception as exc:
        raise HTTPException(400, "请求体必须是 JSON") from exc
    if not isinstance(body, dict):
        raise HTTPException(400, "请求体必须是 JSON 对象")
    return body


def _decode_base64(value: str) -> bytes:
    cleaned = "".join(str(value or "").split())
    if cleaned.startswith("data:"):
        cleaned = cleaned.split(",", 1)[-1]
    if not cleaned:
        raise HTTPException(400, "缺少文件数据")
    # Reject before allocating the decoded buffer (8MB payload ≈ 10.7MB base64).
    if len(cleaned) > 12 * 1024 * 1024:
        raise HTTPException(413, "文件过大（最大 8MB）")
    try:
        return base64.b64decode(cleaned, validate=False)
    except (binascii.Error, ValueError) as e:
        raise HTTPException(400, f"base64 解码失败: {e}") from e


def _clamp_card(card: Dict[str, Any]) -> Dict[str, Any]:
    """Apply the same field limits to imported/AI cards as manual edits."""
    for field, limit in _TEXT_LIMITS.items():
        if field in card:
            # A valid imported card must not silently lose its rules at the manual-editor limit.
            card[field] = str(card.get(field) or "")[:500000]
    for field in _LIST_FIELDS:
        if field in card:
            value = card.get(field)
            if not isinstance(value, list):
                value = []
            card[field] = [str(item).strip()[:500000] for item in value if str(item).strip()][:1000]
    return card


def _get_char(char_id: str) -> Dict[str, Any]:
    card = _store.get_character(char_id)
    if not card:
        raise HTTPException(404, "角色不存在")
    return card


async def _character_mutation(char_id: str):
    _get_char(char_id)
    if not _store.begin_generation(char_id):
        raise HTTPException(409, '这个角色正在回复，请先停止生成再修改')
    try:
        yield
    finally:
        _store.end_generation(char_id)


def _public(card: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(card)
    cid = str(out.get("id") or "")
    if cid and _store.has_avatar(cid):
        out["avatar_url"] = (
            f"/api/roleplay/characters/{cid}/avatar?v={int(out.get('updated_at') or 0)}"
        )
    else:
        out["avatar_url"] = ""
    return out


def _identity_choices(card):
    choices = []
    novel_id = card.get('novel_id') or ''
    if novel_id:
        plan = safe_read_json(os.path.join(NOVELS_DIR, novel_id, 'plan.json'), default={})
        for candidate in novel_cards.build_cards_from_plan(plan, novel_id):
            if candidate['name'] != card['name']:
                choices.append({'id': 'novel:' + candidate['name'], 'name': candidate['name'],
                                **character_to_persona(candidate)})
    if len(choices) < 200:
        choices.extend(_store.persona_choices(card, limit=200-len(choices)))
    return choices[:200]


@router.get('/api/roleplay/characters/{char_id}/identity')
async def get_identity(char_id: str):
    card = _get_char(char_id)
    settings = _store.effective_settings(char_id)
    return {'ok': True, 'identity': _store.identity(char_id),
            'choices': _identity_choices(card), 'compatibility': card_features(card, settings, _store.load_worldinfo(char_id))}


@router.put('/api/roleplay/characters/{char_id}/identity', dependencies=[Depends(_character_mutation)])
async def update_identity(char_id: str, request: Request):
    card = _get_char(char_id)
    body = await _json_body(request)
    source = body.get('source_character')
    if source:
        choice = next((c for c in _identity_choices(card) if c['id'] == source), None)
        if not choice:
            raise HTTPException(400, '玩家身份来源已不存在，请重新选择')
        # A source selection populates the editor; explicit edits remain allowed.
        body = {**choice, **body}
    try:
        identity = _store.update_identity(char_id, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {'ok': True, 'identity': identity,
            'compatibility': card_features(card, _store.effective_settings(char_id), _store.load_worldinfo(char_id))}


async def _original_card(card):
    char_id = card['id']
    try:
        if card.get('market_source'):
            incoming, _ = await _market.card(card['market_source'], card['market_reference'])
        elif card.get('source') == 'png' and _store.has_avatar(char_id):
            with open(_store.avatar_path(char_id), 'rb') as stream:
                incoming, _ = parse_card_bytes(stream.read(_MAX_UPLOAD_BYTES))
        else:
            raise HTTPException(400, '没有可重新读取的原始卡片，请导入原卡文件')
    except (MarketError, CardError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return incoming


@router.get('/api/roleplay/characters/{char_id}/sync-metadata')
async def preview_character_metadata(char_id: str):
    card = _get_char(char_id)
    incoming = await _original_card(card)
    return {'ok': True, 'fields': [
        {'field': key, 'current': card.get(key, ''), 'incoming': incoming.get(key, '')}
        for key in _TEXT_FIELDS + _LIST_FIELDS
        if card.get(key, [] if key in _LIST_FIELDS else '') != incoming.get(key, [] if key in _LIST_FIELDS else '')
    ]}


@router.post('/api/roleplay/characters/{char_id}/sync-metadata', dependencies=[Depends(_character_mutation)])
async def sync_character_metadata(char_id: str, request: Request):
    card = _get_char(char_id)
    body = await _json_body(request) if await request.body() else {}
    incoming = await _original_card(card)
    card = _get_char(char_id)  # Recheck edits made while the shop request was in flight.
    selected = body.get('fields', [])
    if not isinstance(selected, list) or any(key not in _TEXT_FIELDS + _LIST_FIELDS for key in selected):
        raise HTTPException(400, '无效的原卡字段选择')
    expected = body.get('expected', {})
    proposed = body.get('proposed', {})
    if not isinstance(expected, dict) or not isinstance(proposed, dict):
        raise HTTPException(400, '字段预览数据必须是对象')
    for key in selected:
        if expected.get(key) != card.get(key, '') or proposed.get(key) != incoming.get(key, ''):
            raise HTTPException(409, '角色或商店原卡已改变，请重新预览后应用')
    original = {str(e['id']): e for e in worldinfo.entries_from_card(incoming)}
    entries = _store.load_worldinfo(char_id)
    merged = []
    for entry in entries:
        base = original.get(str(entry['id']))
        if base:
            # Old UI could edit these values. New metadata must not overwrite them.
            base = {**base, **{k: entry[k] for k in ('id', 'content', 'comment', 'keys', 'secondary_keys', 'enabled') if k in entry}}
            merged.append(worldinfo.normalize_entry(base))
        else:
            merged.append(entry)
    backup = os.path.join(_store.root, 'backups', 'card-metadata', char_id, str(time.time_ns()))
    os.makedirs(backup, exist_ok=True)
    shutil.copy2(_store.characters_file, os.path.join(backup, 'characters.json'))
    if os.path.isfile(_store.worldinfo_path(char_id)):
        shutil.copy2(_store.worldinfo_path(char_id), os.path.join(backup, 'worldinfo.json'))
    for key in ('card_data', 'card_spec', 'card_spec_version', 'character_book'):
        if key in incoming:
            card[key] = incoming[key]
    for key in selected:
        card[key] = incoming.get(key, [] if key in _LIST_FIELDS else '')
    card['extensions'] = {**(incoming.get('extensions') or {}), **(card.get('extensions') or {})}
    _store.upsert_character(card)
    _store.save_worldinfo(char_id, merged)
    return {'ok': True, 'compatibility': card_features(card, _store.effective_settings(char_id), merged)}


def _apply_card_fields(body: Dict[str, Any], base: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    card = dict(base or {})
    for field in _TEXT_FIELDS:
        if field in body:
            value = body.get(field)
            if value is None:
                value = ""
            if not isinstance(value, str):
                raise HTTPException(400, f"{field} 必须是字符串")
            limit = _TEXT_LIMITS[field] if field in ('name', 'creator', 'character_version') else 500000
            if len(value.strip()) > limit:
                raise HTTPException(400, f'{field} 超过长度限制（{limit} 字符）')
            card[field] = value.strip()
    for field in _LIST_FIELDS:
        if field in body:
            value = body.get(field)
            if isinstance(value, str):
                value = [value]
            if not isinstance(value, list):
                raise HTTPException(400, f"{field} 必须是字符串数组")
            if len(value) > 1000 or any(not isinstance(item, str) or len(item) > 500000 for item in value):
                raise HTTPException(400, f'{field} 必须最多 1000 项，每项最多 500000 字符')
            card[field] = [item.strip() for item in value if item.strip()]
    return card


# -------------------------------------------------------------------- settings

def _book_scope(value):
    novel_id = str(value or '').strip()
    if novel_id:
        _validate_novel_id(novel_id)
        if not engine.get_novel(novel_id):
            raise HTTPException(404, '目标小说不存在')
    return novel_id


@router.get('/api/roleplay/marketplace/catalog')
async def market_catalog(source: str = 'official', q: str = '', page: int = 1):
    try:
        return {'ok': True, **await _market.catalog(source, q, page)}
    except MarketError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post('/api/roleplay/marketplace/preview')
async def market_preview(request: Request):
    body = await _json_body(request)
    try:
        card, _ = await _market.card(str(body.get('source', 'official')), str(body.get('reference', '')))
        return {'ok': True, 'character': _clamp_card(card)}
    except (MarketError, CardError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post('/api/roleplay/marketplace/install')
async def market_install(request: Request):
    body = await _json_body(request)
    novel_id = _book_scope(body.get('novel_id', ''))
    try:
        card, avatar = await _market.card(str(body.get('source', 'official')), str(body.get('reference', '')))
    except (MarketError, CardError) as exc:
        raise HTTPException(400, str(exc)) from exc
    card = _clamp_card(card)
    card['novel_id'] = novel_id
    card['source'] = 'marketplace'
    # Network work finishes before claiming the short storage transaction.
    with _store._lock:
        for existing in _store.list_characters():
            if (existing.get('market_source'), existing.get('market_reference'), existing.get('novel_id', '')) == (card['market_source'], card['market_reference'], novel_id):
                return {'ok': True, 'character': _public(existing), 'reused': True}
        entries = worldinfo.entries_from_card(card)
        card.update(id=uuid.uuid4().hex, created_at=time.time())
        card = _store.upsert_character(card)
        if entries:
            _store.save_worldinfo(card['id'], entries)
        if avatar:
            _store.save_avatar(card['id'], avatar)
    return {'ok': True, 'character': _public(card), 'reused': False, 'worldinfo_imported': len(entries)}


@router.post('/api/roleplay/characters/{char_id}/copy')
async def copy_character(char_id: str, request: Request):
    body = await _json_body(request)
    card = dict(_get_char(char_id))
    card.update(id=uuid.uuid4().hex, novel_id=_book_scope(body.get('novel_id', '')), created_at=time.time())
    card = _store.upsert_character(card)
    if _store.has_avatar(char_id):
        with open(_store.avatar_path(char_id), 'rb') as stream:
            _store.save_avatar(card['id'], stream.read(_MAX_UPLOAD_BYTES))
    entries = _store.load_worldinfo(char_id)
    if entries:
        _store.save_worldinfo(card['id'], entries)
    return {'ok': True, 'character': _public(card)}

@router.get("/api/roleplay/settings")
async def get_settings():
    return {"ok": True, "settings": _store.settings()}


@router.put("/api/roleplay/settings")
async def update_settings(request: Request):
    body = await _json_body(request)
    settings = _store.update_settings(body)
    return {"ok": True, "settings": settings}


# ------------------------------------------------------------------ characters

@router.get("/api/roleplay/characters")
async def list_characters(novel_id: Optional[str] = None, summary: bool = False):
    cards = _store.list_characters(novel_id=novel_id, summary=summary)
    return {"ok": True, "characters": [_public(c) for c in cards]}


@router.get("/api/roleplay/characters/{char_id}")
async def get_character(char_id: str):
    return {"ok": True, "character": _public(_get_char(char_id))}


@router.get('/api/roleplay/characters/{char_id}/export')
async def export_character(char_id: str):
    from urllib.parse import quote
    card = _get_char(char_id)
    data = dict(card.get('card_data') or {})
    data.update({key: card.get(key, '') for key in _TEXT_FIELDS})
    for key in (*_LIST_FIELDS, 'extensions'):
        data[key] = card.get(key, [] if key in _LIST_FIELDS else {})
    entries = _store.load_worldinfo(char_id)
    if entries or card.get('character_book') or data.get('character_book'):
        data['character_book'] = {**(card.get('character_book') or data.get('character_book') or {}), 'entries': worldinfo.export_entries(entries)}
    return Response(json.dumps({'spec': card.get('card_spec', 'chara_card_v2'), 'spec_version': card.get('card_spec_version', '2.0'), 'data': data}, ensure_ascii=False, indent=2),
                    media_type='application/json', headers={'Content-Disposition': "attachment; filename=character.json; filename*=UTF-8''" + quote(card['name']+'.json')})


@router.post("/api/roleplay/characters")
async def create_character(request: Request):
    body = await _json_body(request)
    card = _apply_card_fields(body)
    if not str(card.get("name") or "").strip():
        raise HTTPException(400, "角色名不能为空")
    now = time.time()
    card["id"] = uuid.uuid4().hex
    card["source"] = "manual"
    card['novel_id'] = _book_scope(body.get('novel_id', ''))
    card["created_at"] = now
    card["updated_at"] = now
    _store.upsert_character(card)
    return {"ok": True, "character": _public(card)}


@router.put("/api/roleplay/characters/{char_id}", dependencies=[Depends(_character_mutation)])
async def update_character(char_id: str, request: Request):
    existing = _get_char(char_id)
    body = await _json_body(request)
    card = _apply_card_fields(body, existing)
    if 'novel_id' in body:
        card['novel_id'] = _book_scope(body['novel_id'])
    if not str(card.get("name") or "").strip():
        raise HTTPException(400, "角色名不能为空")
    _store.upsert_character(card)
    return {"ok": True, "character": _public(card)}


@router.delete("/api/roleplay/characters/{char_id}", dependencies=[Depends(_character_mutation)])
async def delete_character(char_id: str):
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
    card['novel_id'] = _book_scope(body.get('novel_id', ''))
    card["created_at"] = now
    card["updated_at"] = now
    book_entries = worldinfo.entries_from_card(card)
    card = _clamp_card(card)
    _store.upsert_character(card)
    if book_entries:
        _store.save_worldinfo(card["id"], book_entries)
    if fmt == "png":
        _store.save_avatar(card["id"], data)
    log.info("Imported roleplay card: %s (%s, lorebook=%d)",
             card["name"], fmt, len(book_entries))
    return {"ok": True, "character": _public(card), "format": fmt,
            "worldinfo_imported": len(book_entries)}


@router.post("/api/roleplay/characters/from-novel/{novel_id}")
async def characters_from_novel(
    novel_id: str,
    request: Request,
    owner_id: str = Depends(require_owner),
):
    """把小说角色宝典导出为角色卡。

    body（可选）:
      {"mode": "ai" | "template", "characters": ["角色名", ...]}

    - ``ai``（推荐）：读取 worldbuilding / character_bible / character_profiles /
      character_voices，调用当前 LLM 生成完整 SillyTavern 角色卡；失败自动回退模板。
    - ``template``：仅把 plan.json 字段结构化拼接，不调用 LLM（兼容旧行为）。
    """
    _validate_novel_id(novel_id)
    if not is_internal_version() and not ownership_store.owns(novel_id, owner_id):
        raise HTTPException(404, "小说不存在")
    novel_dir = os.path.join(NOVELS_DIR, novel_id)
    if not os.path.isdir(novel_dir):
        raise HTTPException(404, "小说不存在")
    plan = safe_read_json(os.path.join(novel_dir, "plan.json"), default=None)
    if not isinstance(plan, dict) or not plan:
        raise HTTPException(404, "该小说还没有生成设定（plan.json）")

    body: Dict[str, Any] = {}
    raw_body = await request.body()
    if raw_body.strip():
        try:
            parsed_body = json.loads(raw_body)
        except Exception as exc:
            raise HTTPException(400, "请求体必须是 JSON") from exc
        if not isinstance(parsed_body, dict):
            raise HTTPException(400, "请求体必须是 JSON 对象")
        body = parsed_body
    mode = str(body.get("mode") or "template").strip().lower()
    if mode not in ("ai", "template"):
        raise HTTPException(400, "mode 只能是 ai 或 template")
    raw_names = body.get("characters") or body.get("names")
    names: Optional[list] = None
    if isinstance(raw_names, list):
        names = [str(name).strip() for name in raw_names if str(name).strip()]

    all_names = novel_cards.plan_character_names(plan)
    if not all_names:
        raise HTTPException(404, "该小说的角色宝典里还没有可用角色")

    existing = _store.list_characters()
    existing_names = {
        str(c.get("name")) for c in existing
        if c.get("source") == "novel" and c.get("novel_id") == novel_id
    }
    requested = [name for name in all_names if not names or name in set(names)]
    pending = [name for name in requested if name not in existing_names]
    skipped = len(requested) - len(pending)

    warnings: List[str] = []
    cards: List[Dict[str, Any]] = []
    if pending:
        if mode == "ai":
            bible = safe_read_json(os.path.join(novel_dir, "character_bible.json"), default={})
            profiles = safe_read_json(os.path.join(novel_dir, "character_profiles.json"), default={})
            voices = safe_read_json(os.path.join(novel_dir, "character_voices.json"), default={})
            cards, warnings = await novel_cards.generate_cards_with_ai(
                engine, plan, novel_id,
                bible=bible if isinstance(bible, dict) else {},
                profiles=profiles if isinstance(profiles, dict) else {},
                voices=voices if isinstance(voices, dict) else {},
                names=pending,
            )
            cards = [card for card in cards if str(card.get("name")) in set(pending)]
        else:
            cards = [
                card for card in novel_cards.build_cards_from_plan(plan, novel_id)
                if str(card.get("name")) in set(pending)
            ]

    created = []
    for card in cards:
        name = str(card.get("name") or "")
        if not name or name in existing_names:
            skipped += 1
            continue
        card = _clamp_card(card)
        card["id"] = uuid.uuid4().hex
        now = time.time()
        card["created_at"] = now
        card["updated_at"] = now
        _store.upsert_character(card)
        created.append(_public(card))
        existing_names.add(name)

    ai_filled = sum(1 for card in created if card.get("ai_filled"))
    log.info(
        "Roleplay export from novel %s: mode=%s created=%d skipped=%d ai_filled=%d",
        novel_id, mode, len(created), skipped, ai_filled,
    )
    return {
        "ok": True,
        "characters": created,
        "created": len(created),
        "skipped": skipped,
        "mode": mode,
        "ai_filled": ai_filled,
        "warnings": warnings,
    }


# --------------------------------------------------------------------- avatars

@router.get("/api/roleplay/characters/{char_id}/avatar")
async def get_avatar(char_id: str):
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


@router.post("/api/roleplay/characters/{char_id}/avatar", dependencies=[Depends(_character_mutation)])
async def upload_avatar(char_id: str, request: Request):
    _get_char(char_id)
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
    card["updated_at"] = time.time()
    _store.upsert_character(card)
    return {"ok": True, "avatar_url": _public(card)["avatar_url"]}


# ----------------------------------------------------------------------- chats

@router.get("/api/roleplay/characters/{char_id}/chat")
async def get_chat(char_id: str):
    card = _get_char(char_id)
    return {"ok": True, "messages": display_messages(_store.chat_messages(char_id), card, _store.effective_settings(char_id)),
            'identity': _store.identity(char_id), 'identity_bound': os.path.isfile(_store.identity_path(char_id))}


@router.post("/api/roleplay/characters/{char_id}/chat/start", dependencies=[Depends(_character_mutation)])
async def start_chat(char_id: str):
    card = _get_char(char_id)
    messages = _engine.ensure_chat(char_id)
    return {"ok": True, "messages": display_messages(messages, card, _store.effective_settings(char_id)), 'identity': _store.identity(char_id)}


@router.post("/api/roleplay/characters/{char_id}/chat/send")
async def send_chat(char_id: str, request: Request):
    _get_char(char_id)
    body = await _json_body(request)
    message = str(body.get("message") or "").strip()
    if not message:
        raise HTTPException(400, "消息不能为空")
    if len(message) > 8000:
        raise HTTPException(400, "消息过长（最多 8000 字）")
    request_id = body.get("request_id")
    if request_id is not None and (
        not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128
    ):
        raise HTTPException(400, "request_id 必须是 1-128 字符的字符串")
    return _sse_response(
        _engine.stream_reply(char_id, message=message, request_id=request_id)
    )


@router.post("/api/roleplay/characters/{char_id}/chat/regenerate")
async def regenerate_chat(char_id: str):
    _get_char(char_id)
    return _sse_response(_engine.stream_reply(char_id, regenerate=True))


@router.post("/api/roleplay/characters/{char_id}/chat/swipe")
async def swipe_chat(char_id: str, request: Request):
    """Cycle existing reply variants (JSON) or generate a new one (SSE)."""
    _get_char(char_id)
    body: Dict[str, Any] = {}
    try:
        raw_body = await request.json()
        if isinstance(raw_body, dict):
            body = raw_body
    except Exception:
        body = {}
    direction = str(body.get("direction") or "next").strip().lower()
    if direction in ("next", "prev"):
        messages = _engine.swipe_existing(char_id, direction)
        if messages is None:
            raise HTTPException(400, "没有可切换的回复版本")
        return {"ok": True, "messages": display_messages(messages, _get_char(char_id), _store.effective_settings(char_id))}
    if direction != "new":
        raise HTTPException(400, "direction 只能是 next / prev / new")
    return _sse_response(_engine.stream_reply(char_id, message="/swipe new"))


# ------------------------------------------------------------------- worldinfo

@router.get("/api/roleplay/characters/{char_id}/worldinfo")
async def list_worldinfo(char_id: str):
    _get_char(char_id)
    entries = _store.load_worldinfo(char_id)
    enabled = sum(1 for entry in entries if entry.get("enabled", True))
    return {"ok": True, "entries": entries, "total": len(entries), "enabled": enabled}


@router.post("/api/roleplay/characters/{char_id}/worldinfo/import", dependencies=[Depends(_character_mutation)])
async def import_worldinfo(char_id: str, request: Request):
    card = _get_char(char_id)
    body = await _json_body(request)
    raw_text = str(body.get("json_text") or "")
    if raw_text:
        data = raw_text.encode("utf-8")
    else:
        data = _decode_base64(str(body.get("data_base64") or ""))
    if not data:
        raise HTTPException(400, "世界书文件为空")
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(413, "世界书文件过大（最大 8MB）")
    try:
        payload = worldinfo.decode_worldinfo_bytes(data)
        entries = worldinfo.parse_worldinfo_payload(payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    previous_count = len(_store.load_worldinfo(char_id))
    try:
        merged = _store.merge_worldinfo(char_id, entries)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    # Keep book-level scan rules as well as entries (V2/V3 envelope or ST book).
    book = payload
    if isinstance(book, dict) and isinstance(book.get('data'), dict):
        book = book['data']
    if isinstance(book, dict) and isinstance(book.get('character_book'), dict):
        book = book['character_book']
    if isinstance(book, dict):
        metadata = {k: v for k, v in book.items() if k != 'entries'}
        card['character_book'] = {**(card.get('character_book') or {}), **metadata}
        _store.upsert_character(card)
    imported = len(merged) - previous_count
    log.info("Imported worldinfo for %s: %d entries", char_id, imported)
    return {"ok": True, "imported": imported, "skipped": len(entries) - imported, "entries": merged}


@router.post("/api/roleplay/characters/{char_id}/worldinfo", dependencies=[Depends(_character_mutation)])
async def replace_worldinfo(char_id: str, request: Request):
    _get_char(char_id)
    body = await _json_body(request)
    raw_entries = body.get("entries")
    if not isinstance(raw_entries, list):
        raise HTTPException(400, "entries 必须是数组")
    if len(raw_entries) > worldinfo.MAX_ENTRIES:
        raise HTTPException(400, "世界书条目过多")
    try:
        entries = _store.save_worldinfo(char_id, raw_entries)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "entries": entries}


@router.patch("/api/roleplay/characters/{char_id}/worldinfo/{entry_id}", dependencies=[Depends(_character_mutation)])
async def update_worldinfo_entry(char_id: str, entry_id: str, request: Request):
    _get_char(char_id)
    body = await _json_body(request)
    entries = _store.load_worldinfo(char_id)
    found = None
    for entry in entries:
        if str(entry.get("id")) == entry_id:
            found = entry
            break
    if found is None:
        raise HTTPException(404, "世界书条目不存在")
    if "enabled" in body:
        if not isinstance(body["enabled"], bool):
            raise HTTPException(400, "enabled 必须是布尔值")
        found["enabled"] = body["enabled"]
    if "content" in body:
        found["content"] = str(body["content"] or "")[:worldinfo.MAX_ENTRY_CHARS]
    if "comment" in body:
        found["comment"] = str(body["comment"] or "")[:200]
    if "keys" in body and isinstance(body["keys"], list):
        found["keys"] = [str(key).strip() for key in body["keys"] if str(key).strip()][:50]
    entries = _store.save_worldinfo(char_id, entries)
    return {"ok": True, "entry": found, "entries": entries}


@router.delete("/api/roleplay/characters/{char_id}/worldinfo/{entry_id}", dependencies=[Depends(_character_mutation)])
async def delete_worldinfo_entry(char_id: str, entry_id: str):
    _get_char(char_id)
    entries = _store.load_worldinfo(char_id)
    remaining = [entry for entry in entries if str(entry.get("id")) != entry_id]
    if len(remaining) == len(entries):
        raise HTTPException(404, "世界书条目不存在")
    entries = _store.save_worldinfo(char_id, remaining)
    return {"ok": True, "entries": entries}


@router.post("/api/roleplay/characters/{char_id}/chat/clear", dependencies=[Depends(_character_mutation)])
async def clear_chat(char_id: str, request: Request):
    _get_char(char_id)
    greeting_index: Optional[int] = None
    try:
        body = await request.json()
        if isinstance(body, dict) and body.get("greeting_index") is not None:
            greeting_index = int(body["greeting_index"])
    except Exception:
        pass
    messages = _engine.reset_chat(char_id, greeting_index)
    return {"ok": True, "messages": display_messages(messages, _get_char(char_id), _store.effective_settings(char_id)), 'identity': _store.identity(char_id)}


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
