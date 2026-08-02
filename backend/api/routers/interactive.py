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
        # v3.5.5: 玩家扮演角色（bible 主角）
        proto = ctx.get("protagonist") or {}
        if proto.get("name"):
            p_ident = proto.get("identity", "")
            p_pers = ""
            pers = proto.get("personality") or {}
            if isinstance(pers, dict):
                p_pers = str(pers.get("true_self") or pers.get("surface") or "")[:120]
            elif isinstance(pers, str):
                p_pers = pers[:120]
            st["player_char"] = {
                "name": proto["name"],
                "identity": str(p_ident)[:80],
                "personality_brief": p_pers,
            }
        # 预置主要角色（从 bible 预览）
        casts = st.get("casts", {})
        for name, info in (ctx.get("casts_preview") or {}).items():
            casts[name] = {"present": True, "profile": {}, "role": info.get("role", "")}
        st["casts"] = casts
        # v3.2: 世界观注入（重开后剧情必须贴合本小说设定）
        wb = ctx.get("worldbuilding") or {}
        # v3.3.1: geography 可能是 str/dict/list，统一转字符串（修复 start 500）
        # v3.5.18: location 只取第一个地点（list 转字符串会显示 JSON 数组字样）
        _geo = wb.get("geography", "")
        if isinstance(_geo, list) and _geo:
            _geo = _geo[0] if isinstance(_geo[0], str) else json.dumps(_geo[0], ensure_ascii=False)
        elif isinstance(_geo, dict):
            _geo = json.dumps(_geo, ensure_ascii=False)
        _loc = wb.get("starting_location") or str(_geo) or ""
        # v3.5.36: 地点清洗——geography 常是'城市，区域A、区域B…'列表式字符串，
        # 只取第一段（城市+首个区域），防止整串地点注入导致'场景切到列表里别的地点'
        if _loc:
            _loc = str(_loc).strip()
            for _sep in ("，", ",", "；", ";"):
                if _sep in _loc:
                    _loc = _loc.split(_sep)[0].strip()
                    break
        st["state"]["location"] = _loc[:60]
        core_conflict = wb.get("core_conflict", "")
        if isinstance(core_conflict, (dict, list)):
            core_conflict = json.dumps(core_conflict, ensure_ascii=False)
        st["state"]["objective"] = str(core_conflict) or "踏上你的旅程"
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
        # v3.5.20: 复用全局状态资源——时间线摘要 + 未回收伏笔（剧情呼应）
        try:
            import os
            from config import NOVELS_DIR
            gs_path = os.path.join(NOVELS_DIR, novel_id, "global_state.json")
            if os.path.exists(gs_path):
                with open(gs_path, "r", encoding="utf-8") as f:
                    gs = json.load(f)
                tl = gs.get("timeline") or []
                if isinstance(tl, list) and tl:
                    st["timeline_brief"] = " → ".join(str(x)[:40] for x in tl[-5:])
                chs = gs.get("chapters_summary") or ""
                if isinstance(chs, str) and chs.strip():
                    st["chapters_brief"] = str(chs)[:200]
            fs_path = os.path.join(NOVELS_DIR, novel_id, "foreshadowing.json")
            if os.path.exists(fs_path):
                with open(fs_path, "r", encoding="utf-8") as f:
                    fs = json.load(f)
                if isinstance(fs, list):
                    unclosed = [x for x in fs if isinstance(x, dict) and not x.get("resolved")]
                    if unclosed:
                        st["foreshadows_brief"] = "；".join(
                            str(x.get("content") or x.get("seed") or "")[:50]
                            for x in unclosed[:3])
        except Exception as e:
            log.warning(f"global resources load failed: {e}")
        _store.save_state(novel_id, st)

    # v3.5.28: 大纲驱动互动——每次 start 都刷新章节列表（老存档也补上），
    # 互动剧情按 plan.outline 章节推进（不在初始化块内：老存档 scene_num>0 会跳过）
    try:
        import os as _os
        from config import NOVELS_DIR as _ND
        _plan_path = _os.path.join(_ND, novel_id, "plan.json")
        if _os.path.exists(_plan_path):
            with open(_plan_path, "r", encoding="utf-8") as _f:
                _plan = json.load(_f)
            _out = _plan.get("outline") or {}
            _chs = []
            for _v in (_out.get("volumes") or []):
                for _c in (_v.get("chapters") or []):
                    _chs.append({
                        "number": int(_c.get("number", len(_chs) + 1)),
                        "title": str(_c.get("title", ""))[:30],
                        "summary": str(_c.get("summary", ""))[:180],
                        "volume": str(_v.get("title", ""))[:20],
                        "target_words": int(_c.get("target_words", 0) or 0),
                    })
            if _chs:
                _st2 = _store.load_state(novel_id) or {}
                _st2["outline_chapters"] = _chs
                _op = _st2.get("outline_progress") or {}
                if not _op:
                    # v3.5.28: 老存档按已玩场景数估算大纲进度（避免剧情倒流）
                    _sn = int((_st2.get("scene_num") or 0))
                    _est = max(0, min((_sn - 1) // 3, len(_chs) - 1)) if _sn > 0 else 0
                    _st2["outline_progress"] = {
                        "idx": _est,
                        "scene_in_chapter": (_sn - 1) % 3 if _sn > 0 else 0,
                    }
                    _st2["state"]["objective"] = (
                        f"【{_chs[_est]['title']}】{_chs[_est]['summary']}"[:220]
                        or _st2["state"]["objective"])
                _store.save_state(novel_id, _st2)
    except Exception as e:
        log.warning(f"outline load failed: {e}")

    # v3.5.34: 文风继承——每次 start 从小说风格配置生成风格描述
    # （文笔/语气/对话/反套路），注入互动全部生成路径，保证与小说文风一致
    try:
        from core.planner import get_style, build_style_prompt
        _st3 = _store.load_state(novel_id) or {}
        _sn = _st3.get("style", "") or ""
        if _sn:
            _sb = build_style_prompt(get_style(_sn))
            if _sb:
                _st3["style_brief"] = _sb[:600]
                _store.save_state(novel_id, _st3)
    except Exception as e:
        log.warning(f"style_brief failed: {e}")

    async def event_stream():
        # 挂载出场角色人设（异步后台，不阻塞开场）
        try:
            state = _store.load_state(novel_id)
            if state:
                _story.attach_cast_profiles(novel_id, list(state.get("casts", {}).keys()))
        except Exception as e:
            log.warning(f"attach_cast_profiles failed: {e}")
        # v3.5.13: 开场背景介绍——缓存命中先发（老玩家秒看）；未缓存则与场景并行
        # 生成（不阻塞场景流，避免首次进入 25s+）
        # v3.5.18: 旧缓存过浅（<200 字）强制重新生成
        intro_fut = None
        try:
            state = _store.load_state(novel_id)
            if state:
                cached_intro = state.get("intro") or ""
                # v3.5.33: 精简版 250-350 字——旧缓存 >500 字（长篇）也强制重生
                if cached_intro and 200 <= len(cached_intro) <= 400:
                    yield f"data: {json.dumps({'type': 'intro', 'content': cached_intro}, ensure_ascii=False)}\n\n"
                else:
                    intro_fut = asyncio.create_task(
                        asyncio.to_thread(_story.generate_intro, novel_id, state,
                                          force=bool(cached_intro)))
        except Exception as e:
            log.warning(f"intro pre failed: {e}")
        async for data in _sse_with_heartbeat(_story.generate_scene_stream(novel_id)):
            yield data
        # 场景流结束后拿并行生成的 intro（不阻塞场景本身）
        if intro_fut is not None:
            try:
                intro = await asyncio.wait_for(intro_fut, timeout=30)
                if intro:
                    yield f"data: {json.dumps({'type': 'intro', 'content': intro}, ensure_ascii=False)}\n\n"
            except Exception as e:
                log.warning(f"intro parallel failed: {e}")

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
        # v3.3.1: missing hooks 回流——未达成的对话目标写入 state，
        # 下一段场景生成时作为"未兑现的因果"软约束（后果显现/角色惦记）
        if hooks_result.get("missing"):
            state["pending_missing_hooks"] = hooks_result["missing"][:3]
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
    """重置互动存档（旧存档备份到 backup-<ts>/）

    v3.5.30: 重开前后台把当前章的互动剧情沉淀为章节正文（玩到一半不白玩）
    """
    _validate_novel_id(novel_id)
    try:
        _st = _store.load_state(novel_id)
        if _st:
            _chs = _st.get("outline_chapters") or []
            _op = _st.get("outline_progress") or {}
            _idx = int(_op.get("idx", 0))
            if _chs and not _op.get("final_done") and _idx < len(_chs):
                import threading
                threading.Thread(
                    target=_story._sync_chapter_from_interactive,
                    args=(novel_id, _idx,
                          int(_op.get("scene_start", 1) or 1),
                          int(_st.get("scene_num", 0) or 0)),
                    daemon=True,
                ).start()
    except Exception as e:
        log.warning(f"restart pre-sync failed: {e}")
    ok = _store.restart(novel_id)
    if not ok:
        raise HTTPException(500, "重开失败（备份失败）")
    return {"ok": True, "message": "互动存档已重置"}
