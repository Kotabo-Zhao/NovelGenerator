"""NovelGenerator — 互动小说模式 API Router（v3.0）

端点（docs/interactive-novel-plan.html §10）：
POST  /api/novels/{novel_id}/interactive/start      初始化互动存档 + 生成开场场景（SSE）
POST  /api/novels/{novel_id}/interactive/scene      生成下一场景（SSE）
POST  /api/novels/{novel_id}/interactive/chat       对话（SSE，body: message + @角色）
POST  /api/novels/{novel_id}/interactive/end-chat   PACT 提取：对话 → 剧情事实
GET   /api/novels/{novel_id}/interactive/state      读取互动存档
GET   /api/novels/{novel_id}/interactive/voices     读取角色音色配置
PUT   /api/novels/{novel_id}/interactive/voices     覆盖角色音色
POST  /api/novels/{novel_id}/interactive/rollback   回退一步
POST  /api/novels/{novel_id}/interactive/restart    重开
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional

from ..deps import engine, _validate_novel_id, _sse_with_heartbeat

log = logging.getLogger(__name__)
router = APIRouter()

from core.interactive.interact_store import InteractStore
from core.interactive.story_director import StoryDirector
from core.interactive.dialogue_engine import DialogueEngine
from core.interactive import voice_director
from config import NOVELS_DIR

# ── 单例（复用 engine 的 client/model）──
_store = InteractStore(NOVELS_DIR)
_story = StoryDirector(engine.client, engine.model, _store, engine=engine)
_chat = DialogueEngine(engine.client, engine.model, _store, engine=engine)


class ChatRequest(BaseModel):
    message: str
    target: Optional[str] = None


class VoiceOverrideRequest(BaseModel):
    voice: str
    rate: str = "+0%"
    pitch: str = "+0Hz"


# ── start：初始化 + 开场场景 ──
@router.post("/api/novels/{novel_id}/interactive/start")
async def interactive_start(novel_id: str):
    """进入互动模式：初始化存档（或复用已有）+ 生成开场场景（SSE）"""
    _validate_novel_id(novel_id)
    try:
        ctx = _story.build_context_from_bible(novel_id)
    except Exception as e:
        log.warning(f"build_context_from_bible failed: {e}")
        ctx = {"title": novel_id, "genre": "", "style": "", "protagonist_name": ""}

    if not _store.exists(novel_id) or not _store.load_state(novel_id).get("scene_num", 0):
        from core.interactive.interact_store import new_state
        st = new_state(novel_id, ctx.get("title", novel_id),
                       ctx.get("genre", ""), ctx.get("style", ""),
                       ctx.get("protagonist_name", ""))
        # 预置主要角色（从 bible 预览）
        casts = st.get("casts", {})
        for name, info in (ctx.get("casts_preview") or {}).items():
            casts[name] = {"present": True, "profile": {}, "role": info.get("role", "")}
        st["casts"] = casts
        # v3.2: 世界观注入（重开后剧情必须贴合本小说设定）
        wb = ctx.get("worldbuilding") or {}
        st["state"]["location"] = wb.get("starting_location") or wb.get("geography", "")[:60] or ""
        st["state"]["objective"] = wb.get("core_conflict", "") or "踏上你的旅程"
        wb_brief = []
        for k in ("era", "geography", "power_system", "core_conflict", "factions"):
            v = wb.get(k)
            if isinstance(v, str) and v.strip():
                wb_brief.append(f"{k}: {v.strip()[:120]}")
            elif isinstance(v, dict) and v:
                wb_brief.append(f"{k}: {json.dumps(v, ensure_ascii=False)[:120]}")
            elif isinstance(v, list) and v:
                wb_brief.append(f"{k}: {'、'.join(str(x)[:40] for x in v[:4])[:120]}")
        st["worldbuilding_brief"] = "\n".join(wb_brief)[:800]
        _store.save_state(novel_id, st)

    async def event_stream():
        # 挂载出场角色人设（异步后台，不阻塞开场）
        try:
            state = _store.load_state(novel_id)
            if state:
                _story.attach_cast_profiles(novel_id, list(state.get("casts", {}).keys()))
        except Exception as e:
            log.warning(f"attach_cast_profiles failed: {e}")
        async for data in _sse_with_heartbeat(_story.generate_scene_stream(novel_id)):
            yield data

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── scene：推进剧情 ──
@router.post("/api/novels/{novel_id}/interactive/scene")
async def interactive_scene(novel_id: str):
    """生成下一场景（SSE）"""
    _validate_novel_id(novel_id)
    if not _store.exists(novel_id):
        raise HTTPException(404, "互动存档不存在，请先 start")

    async def event_stream():
        async for data in _sse_with_heartbeat(_story.generate_scene_stream(novel_id)):
            yield data

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── chat：对话 ──
@router.post("/api/novels/{novel_id}/interactive/chat")
async def interactive_chat(novel_id: str, req: ChatRequest):
    """玩家发言 → 角色回复（SSE）"""
    _validate_novel_id(novel_id)
    if not req.message or not req.message.strip():
        raise HTTPException(400, "消息不能为空")
    if len(req.message) > 500:
        raise HTTPException(400, "消息过长（最多 500 字）")
    if not _store.exists(novel_id):
        raise HTTPException(404, "互动存档不存在，请先 start")

    async def event_stream():
        async for data in _sse_with_heartbeat(
            _chat.chat_stream(novel_id, req.message, req.target)
        ):
            yield data

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── end-chat：PACT 提取 ──
@router.post("/api/novels/{novel_id}/interactive/end-chat")
async def interactive_end_chat(novel_id: str):
    """结束对话：PACT 提取对话 → 剧情事实（facts）+ 关系更新 + Agenda 钩子核对"""
    _validate_novel_id(novel_id)
    if not _store.exists(novel_id):
        raise HTTPException(404, "互动存档不存在，请先 start")

    chat_entries = _store.recent_chats(novel_id, 40)
    # 只取本轮对话（从上次 end-chat 之后）
    state = _store.load_state(novel_id)
    last_chat_scene = state.get("_last_chat_scene", 0)
    result = await asyncio.to_thread(_story.extract_pact, novel_id, chat_entries)

    state = _store.load_state(novel_id)
    # v3.3: Agenda 钩子核对（对话是否推进了剧情开关）
    agenda = state.get("agenda")
    hooks_result = {}
    if agenda:
        hooks_result = await asyncio.to_thread(_story.verify_hooks, novel_id, agenda)
    # 核对完成后清除议程（本轮对话的轨道使命结束，下一节点重新生成）
    if agenda:
        state.pop("agenda", None)
        state.pop("drift_note", None)
        _store.save_state(novel_id, state)
    return {
        "ok": True,
        "facts": state.get("facts", [])[-10:],
        "relations": state.get("state", {}).get("relations", {}),
        "objective": state.get("state", {}).get("objective", ""),
        "tone": result.get("tone", ""),
        "hooks": hooks_result,   # {hook_hits, all_hit, missing}
    }


# ── state：读取存档 ──
@router.get("/api/novels/{novel_id}/interactive/state")
async def interactive_state(novel_id: str):
    """读取互动存档（断线重连/刷新恢复）"""
    _validate_novel_id(novel_id)
    state = _store.load_state(novel_id)
    if state is None:
        raise HTTPException(404, "互动存档不存在，请先 start")
    # 精简返回（不含过大的 profile）
    out = dict(state)
    casts = {}
    for name, c in (state.get("casts") or {}).items():
        cc = dict(c)
        prof = cc.get("profile") or {}
        cc["profile_brief"] = {
            "dna": [str(d.get("name", d))[:50] if isinstance(d, dict) else str(d)[:50]
                    for d in prof.get("expression_dna", [])[:3]],
            "anti": [str(a.get("pattern", a))[:50] if isinstance(a, dict) else str(a)[:50]
                     for a in prof.get("anti_patterns", [])[:2]],
        }
        cc.pop("profile", None)
        casts[name] = cc
    out["casts"] = casts
    # v3.2: 返回最近场景的完整 blocks（前端切回时恢复显示）
    try:
        scenes = _store.recent_scenes(novel_id, 1)
        if scenes:
            out["recent_blocks"] = scenes[-1].get("blocks", [])
            out["recent_scene_num"] = scenes[-1].get("scene_num", 0)
        else:
            out["recent_blocks"] = []
            out["recent_scene_num"] = 0
    except Exception as e:
        log.warning(f"recent_blocks failed: {e}")
        out["recent_blocks"] = []
        out["recent_scene_num"] = 0
    return {"ok": True, "state": out}


# ── voices：音色配置 ──
@router.get("/api/novels/{novel_id}/interactive/voices")
async def interactive_voices(novel_id: str):
    """读取全部角色音色配置（规则映射 + 玩家覆盖）"""
    _validate_novel_id(novel_id)
    state = _store.load_state(novel_id)
    if state is None:
        raise HTTPException(404, "互动存档不存在，请先 start")
    overrides = _store.get_voice_overrides(novel_id)
    result = {}
    for name, c in (state.get("casts") or {}).items():
        prof = c.get("profile") or {}
        desc = " ".join(
            [str(d.get("name", d))[:60] if isinstance(d, dict) else str(d)[:60]
             for d in prof.get("expression_dna", [])[:3]]
        )
        gender = ""
        cfg = voice_director.resolve_voice(_store, novel_id, name, desc, gender)
        result[name] = cfg
    result["_default"] = {"voice": voice_director.DEFAULT_VOICE, "rate": "+0%", "pitch": "+0Hz"}
    result["_male_default"] = {"voice": voice_director.DEFAULT_MALE, "rate": "+0%", "pitch": "+0Hz"}
    return {"ok": True, "voices": result}


@router.put("/api/novels/{novel_id}/interactive/voices/{char_name}")
async def interactive_voice_override(novel_id: str, char_name: str, req: VoiceOverrideRequest):
    """玩家覆盖指定角色音色"""
    _validate_novel_id(novel_id)
    _store.set_voice_override(novel_id, char_name, {
        "voice": req.voice, "rate": req.rate, "pitch": req.pitch,
    })
    return {"ok": True}


@router.delete("/api/novels/{novel_id}/interactive/voices/{char_name}")
async def interactive_voice_reset(novel_id: str, char_name: str):
    """恢复默认音色（删除玩家覆盖）"""
    _validate_novel_id(novel_id)
    over = _store.get_voice_overrides(novel_id)
    if char_name in over:
        del over[char_name]
        _store.clear_voice_overrides(novel_id)
        for k, v in over.items():
            _store.set_voice_override(novel_id, k, v)
    return {"ok": True}


# ── rollback：回退一步 ──
@router.post("/api/novels/{novel_id}/interactive/rollback")
async def interactive_rollback(novel_id: str):
    """回退到最近一份快照（玩家反悔）"""
    _validate_novel_id(novel_id)
    ok = _store.rollback(novel_id)
    if not ok:
        raise HTTPException(400, "没有可回退的快照")
    return {"ok": True}


# ── restart：重开 ──
@router.post("/api/novels/{novel_id}/interactive/restart")
async def interactive_restart(novel_id: str):
    """重置互动存档（旧存档备份到 backup-<ts>/）"""
    _validate_novel_id(novel_id)
    ok = _store.restart(novel_id)
    if not ok:
        raise HTTPException(500, "重开失败（备份失败）")
    return {"ok": True, "message": "互动存档已重置"}
