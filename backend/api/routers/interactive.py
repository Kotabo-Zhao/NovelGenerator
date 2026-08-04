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
import time

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
    # v3.6.4: force_talk=True → 纯对话（跳过行动识别，输入框专用）
    force_talk: Optional[bool] = False


class VoiceOverrideRequest(BaseModel):
    voice: str
    rate: str = "+0%"
    pitch: str = "+0Hz"


class StartRequest(BaseModel):
    character: Optional[str] = None   # v2.5.62: 玩家选择扮演的角色名（缺省 = 主角）


# ── start：初始化 + 开场场景 ──
@router.post("/api/novels/{novel_id}/interactive/start")
async def interactive_start(novel_id: str, req: Optional[StartRequest] = None):
    """进入互动模式：初始化存档（或复用已有）+ 生成开场场景（SSE）

    v2.5.62: body 可带 character（玩家选择扮演的角色名，缺省=主角）
    """
    _validate_novel_id(novel_id)
    char_choice = (req.character if req else None) or None
    try:
        ctx = _story.build_context_from_bible(novel_id)
    except Exception as e:
        log.warning(f"build_context_from_bible failed: {e}")
        ctx = {"title": novel_id, "genre": "", "style": "", "protagonist_name": ""}

    # v2.5.63: 老存档角色切换——玩家重新选择角色时必须生效（不依赖 scene_num==0）
    # 原来 choose_char_apply 只在新存档块内执行 → 已有存档时选谁都被忽略（视角 bug 根因）
    try:
        _exist_st = _store.load_state(novel_id) if _store.exists(novel_id) else None
        _cur_char = ((_exist_st or {}).get("player_char") or {}).get("name", "")
    except Exception:
        _exist_st, _cur_char = None, ""
    _role_switch = bool(char_choice and _exist_st and char_choice != _cur_char)

    if not _store.exists(novel_id) or not _store.load_state(novel_id).get("scene_num", 0):
        from core.interactive.interact_store import new_state
        st = new_state(novel_id, ctx.get("title", novel_id),
                       ctx.get("genre", ""), ctx.get("style", ""),
                       ctx.get("protagonist_name", ""))
        # v2.5.62: 角色选择扮演——全角色预设构建 + 选择应用（缺省=主角）
        from core.interactive.story_director import cast_presets_build, choose_char_apply
        _presets = cast_presets_build(_story._load_plan(novel_id)) if hasattr(_story, '_load_plan') else []
        if not _presets:
            # 兜底：从 ctx 构建（plan 缺失时）
            _proto = ctx.get("protagonist") or {}
            if _proto.get("name"):
                _presets = [{
                    "name": _proto["name"], "identity": str(_proto.get("identity", ""))[:80],
                    "personality": str(_proto.get("personality", ""))[:120],
                    "backstory": "", "motivation": "", "speak_style": "",
                    "initial_attitude": "", "role": "protagonist",
                }]
        # v2.5.63: 同名角色去重保留第一个（supporting/antagonist 可能重复，
        # dict 推导会被后者覆盖 → 档案错乱，与 cast-options 的 seen 去重保持一致）
        _presets_map = {}
        for _p in _presets:
            if _p.get("name") and _p["name"] not in _presets_map:
                _presets_map[_p["name"]] = _p
        _target = char_choice or ctx.get("protagonist_name") or (
            _presets[0]["name"] if _presets else "")
        ok, msg = choose_char_apply(st, _target, _presets_map)
        if not ok and char_choice:
            # 玩家显式选择但角色不存在 → 拒绝启动（前端可重选）
            raise HTTPException(400, f"角色选择失败: {msg}")
        if not ok:
            # 缺省角色也失败（数据异常）→ 保底直接设主角名
            _proto = ctx.get("protagonist") or {}
            if _proto.get("name"):
                st["player_char"] = {"name": _proto["name"],
                                     "identity": str(_proto.get("identity", ""))[:80],
                                     "personality_brief": ""}
        # v3.5.5: 玩家角色存档标记（场景 prompt 注入视角用）
        st.setdefault("cast_choices", {"char": _target, "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
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
        # v3.5.37: 主角状态卡初始化（精确位置由场景后后台提取更新）
        st["player_state"] = {"location": _loc[:60], "time": "",
                              "with": [], "holding": [], "situation": ""}
        core_conflict = wb.get("core_conflict", "")
        if isinstance(core_conflict, (dict, list)):
            core_conflict = json.dumps(core_conflict, ensure_ascii=False)
        st["state"]["objective"] = str(core_conflict) or "踏上你的旅程"
        # v3.6: 世界状态三支柱初始化（时间/地点/人物 + 地点图谱构建）
        try:
            from core.interactive.world_state import ensure_world
            ensure_world(st)
        except Exception as e:
            log.warning(f"world init failed: {e}")
        # v3.6: 图谱 desc 后台一次性补全（LLM 填内容，不阻塞 start）
        try:
            import threading as _th
            _th.Thread(target=_story.enrich_location_descs, args=(novel_id, st),
                       daemon=True).start()
        except Exception as e:
            log.warning(f"desc enrich trigger failed: {e}")
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

    # v2.5.63: 老存档角色切换（视角 bug 修复核心）
    # 已有存档 + 玩家显式选了不同角色 → 重新应用角色选择：
    # - player_char 换成新角色（casts 同步：新角色移除 NPC 化，旧角色回归 NPC）
    # - intro 缓存作废（旧视角开场白不能继续用）
    # - 玩家视角缓存作废（recent_blocks 里混着旧角色的台词）
    if _role_switch:
        try:
            from core.interactive.story_director import cast_presets_build, choose_char_apply
            _st_rs = _store.load_state(novel_id) or {}
            _plan_rs = _story._load_plan(novel_id) if hasattr(_story, '_load_plan') else {}
            _presets_rs = cast_presets_build(_plan_rs)
            _map_rs = {}
            for _p in _presets_rs:  # v2.5.63: 同名去重保留第一个（防档案被冲突描述覆盖）
                if _p.get("name") and _p["name"] not in _map_rs:
                    _map_rs[_p["name"]] = _p
            _ok_rs, _msg_rs = choose_char_apply(_st_rs, char_choice, _map_rs)
            if _ok_rs:
                _st_rs["cast_choices"] = {"char": char_choice, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
                _st_rs.pop("intro", None)            # 旧视角开场白作废
                _st_rs.pop("recent_blocks", None)    # 旧角色对话流作废（防视角混淆）
                _st_rs.pop("drift_note", None)
                _st_rs.pop("pending_travel", None)
                # 主角状态卡同步（场景生成用 player_state 注入视角）
                _ps_rs = _st_rs.get("player_state") or {}
                _ps_rs["name"] = char_choice
                _ps_rs["situation"] = f"已切换扮演角色：{char_choice}"
                _st_rs["player_state"] = _ps_rs
                _store.save_state(novel_id, _st_rs)
                log.info(f"角色切换: {_cur_char} → {char_choice}（老存档视角已重置）")
            else:
                log.warning(f"角色切换失败({char_choice}): {_msg_rs}")
        except Exception as e:
            log.warning(f"role switch failed: {e}")

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
                    _sb = _c.get("scene_beats") or []
                    _chs.append({
                        "number": int(_c.get("number", len(_chs) + 1)),
                        "title": str(_c.get("title", ""))[:30],
                        "summary": str(_c.get("summary", ""))[:180],
                        "volume": str(_v.get("title", ""))[:20],
                        "target_words": int(_c.get("target_words", 0) or 0),
                        # v3.5.54: Galgame 节点图——大纲生成时规划的关键节点，
                        # 互动模式据此收束剧情（每场景推进 1 个节点）
                        "scene_beats": [{"beat": int(b.get("beat", i + 1)),
                                         "name": str(b.get("name", ""))[:20],
                                         "key_action": str(b.get("key_action", ""))[:80]}
                                        for i, b in enumerate(_sb)
                                        if isinstance(b, dict)][:6],
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

    # v2.5.61: 回流补漏——互动已完成但正式章节缺失的自动补（后台，幂等）
    try:
        import threading
        threading.Thread(target=_story.backfill_sync, args=(novel_id,), daemon=True).start()
    except Exception as e:
        log.warning(f"backfill_sync trigger failed: {e}")

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
                # v2.5.63: 缓存绑定角色——intro_char 与当前 player_char 不符时
                # 强制重新生成（角色切换后旧视角开场白不得复用）
                _pc_name = ((state.get("player_char") or {}).get("name", ""))
                _intro_char = state.get("intro_char") or ""
                _intro_mismatch = bool(_pc_name and _intro_char and _pc_name != _intro_char)
                # v3.5.33: 精简版 250-350 字——旧缓存 >500 字（长篇）也强制重生
                if cached_intro and 200 <= len(cached_intro) <= 400 and not _intro_mismatch:
                    yield f"data: {json.dumps({'type': 'intro', 'content': cached_intro}, ensure_ascii=False)}\n\n"
                else:
                    intro_fut = asyncio.create_task(
                        asyncio.to_thread(_story.generate_intro, novel_id, state,
                                          force=bool(cached_intro) or _intro_mismatch))
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
            _chat.chat_stream(novel_id, req.message, req.target,
                              force_talk=bool(req.force_talk))
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
    # v3.5.39: 对话后后台更新主角状态卡（situation/with/condition 随对话变化——
    # 谈崩了/承诺了/关系变了都要反映，等下一场景才更新太慢）
    try:
        _transcript = "\n".join(
            f"{h.get('speaker', h.get('role', ''))}: {str(h.get('content', ''))[:100]}"
            for h in chat_entries[-10:])
        if _transcript:
            import threading
            threading.Thread(
                target=_story._extract_player_state,
                args=(novel_id, "本轮对话：\n" + _transcript),
                daemon=True,
            ).start()
    except Exception as e:
        log.warning(f"post-chat player_state update failed: {e}")
    # v2.5.59: 对话后刷新建议选项（贴合最新剧情进度——旧选项与现状脱节问题）
    _sug = None
    try:
        _st2 = _store.load_state(novel_id) or {}
        _chars2 = _st2.get("node_chars") or []
        if _st2.get("pending_node") and _chars2:
            from core.interactive.story_director import generate_suggestions
            _sug = generate_suggestions(engine.client, engine.model, _store, _st2, _chars2)
            if _sug:
                _st2["suggestions"] = _sug
                _store.save_state(novel_id, _st2)
    except Exception as e:
        log.warning(f"post-chat suggestions refresh failed: {e}")
    return {
        "ok": True,
        "facts": state.get("facts", [])[-10:],
        "relations": state.get("state", {}).get("relations", {}),
        "objective": state.get("state", {}).get("objective", ""),
        "tone": result.get("tone", ""),
        "hooks": hooks_result,   # {hook_hits, all_hit, missing}
        "suggestions": _sug or [],  # v2.5.59: 对话后刷新建议选项
    }


# ── state：读取存档 ──
# ── v2.5.62: 可扮演角色列表（进入互动前选择）──
@router.get("/api/novels/{novel_id}/interactive/cast-options")
async def interactive_cast_options(novel_id: str):
    """返回所有可扮演角色（从 plan.json 构建预设，供前端角色选择卡片）"""
    _validate_novel_id(novel_id)
    from core.interactive.story_director import cast_presets_build
    plan = _story._load_plan(novel_id)
    presets = cast_presets_build(plan)
    if not presets:
        # 兜底：从 bible 上下文取主角
        ctx = _story.build_context_from_bible(novel_id)
        _proto = ctx.get("protagonist") or {}
        if _proto.get("name"):
            presets = [{
                "name": _proto["name"],
                "identity": str(_proto.get("identity", ""))[:80],
                "personality": str(_proto.get("personality", ""))[:120],
                "role": "protagonist",
            }]
    opts = [{
        "name": p.get("name", ""),
        "identity": p.get("identity", ""),
        "personality": p.get("personality", "")[:80],
        "speak_style": p.get("speak_style", "")[:60],
        "role": p.get("role", ""),
    } for p in presets if p.get("name")]
    # v2.5.62: 同名角色去重（supporting/antagonist 可能重复，保留第一个）
    seen = set()
    dedup = []
    for o in opts:
        if o["name"] not in seen:
            seen.add(o["name"])
            dedup.append(o)
    return {"ok": True, "options": dedup}


@router.get("/api/novels/{novel_id}/interactive/state")
async def interactive_state(novel_id: str):
    """读取互动存档（断线重连/刷新恢复）"""
    _validate_novel_id(novel_id)
    state = _store.load_state(novel_id)
    if state is None:
        raise HTTPException(404, "互动存档不存在，请先 start")
    # v3.5.42: 旧存档迁移——recent_blocks 明显不完整（少于场景数）时从 scene_logs 重建
    try:
        _scene_count = len(_store.recent_scenes(novel_id, 60))
        if _scene_count > 3 and len(state.get("recent_blocks") or []) < _scene_count:
            _rb = []
            for _sc in _store.recent_scenes(novel_id, 30):
                _rb.extend(_sc.get("blocks") or [])
            if _rb:
                state["recent_blocks"] = _rb[-260:]
                _store.save_state(novel_id, state)
    except Exception as e:
        log.warning(f"recent_blocks migrate failed: {e}")
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
    # v3.5.42: 返回完整 recent_blocks（场景+对话+行动，切回时恢复全部进度；
    # 旧逻辑只返回最近 1 场景——切回剧情对不上的根因）
    out["recent_blocks"] = state.get("recent_blocks") or []
    out["recent_scene_num"] = state.get("scene_num", 0) or 0
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


# ── v3.6.4: 行动按钮化（方案C）——按钮点击 = 意图已确定，零 LLM 意图识别 ──
class ActRequest(BaseModel):
    action_id: Optional[str] = None   # 按钮 id（action_options 返回）
    intent: Optional[str] = None      # travel/talk/act
    target: Optional[str] = None      # 目标（地点/角色/物品）
    message: Optional[str] = None     # talk 时的说话内容


@router.get("/api/novels/{novel_id}/interactive/actions")
async def interactive_actions(novel_id: str):
    """返回当前上下文可执行的行动按钮列表（规则生成，零 LLM）。

    来源：地点图谱 connected（移动）+ 在场角色（交互）+ 物品（使用）。
    前端渲染成按钮条，点击 → POST /act 直接执行。
    """
    _validate_novel_id(novel_id)
    if not _store.exists(novel_id):
        raise HTTPException(404, "互动存档不存在，请先 start")
    try:
        from core.interactive.world_state import action_options
        st = _store.load_state(novel_id) or {}
        opts = action_options(st)
        return {"ok": True, "actions": opts,
                "hint": "点按钮执行行动；输入框纯对话"}
    except Exception as e:
        log.warning(f"actions failed: {e}")
        return {"ok": False, "actions": [], "error": str(e)[:120]}


@router.post("/api/novels/{novel_id}/interactive/act")
async def interactive_act(novel_id: str, req: ActRequest):
    """直接执行按钮行动（跳过 detect_action 意图识别）。

    - travel: 规则执行器移动（图谱校验/时间推进/在场重算）
    - talk:   转对话引擎（对目标角色说话）
    - act:    通用行动执行（物品使用等）
    返回 SSE：与 chat 相同的 action 事件流。
    """
    _validate_novel_id(novel_id)
    if not _store.exists(novel_id):
        raise HTTPException(404, "互动存档不存在，请先 start")

    intent = (req.intent or "").strip()
    target = (req.target or "").strip()
    if not intent and req.action_id:
        # 从 action_id 反推（go_X / talk_X / use_X）
        if req.action_id.startswith("go_"):
            intent, target = "travel", req.action_id[3:]
        elif req.action_id.startswith("talk_"):
            intent, target = "talk", req.action_id[5:]
        elif req.action_id.startswith("use_"):
            intent, target = "act", req.action_id[4:]
        else:
            intent = "act"
    if intent not in ("travel", "talk", "act"):
        raise HTTPException(400, f"不支持的行动类型: {intent}")

    if intent == "talk":
        # 对角色说话（纯对话，force_talk 由前端走 chat 接口即可）
        # 这里支持 message 直接说话
        if not req.message:
            raise HTTPException(400, "talk 行动需要 message")
        async def talk_stream():
            async for data in _sse_with_heartbeat(
                _chat.chat_stream(novel_id, req.message, target or None, force_talk=True)
            ):
                yield data
        return StreamingResponse(talk_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # travel / act：构造确定性 action dict（跳过 LLM 意图识别）
    if intent == "travel":
        if not target:
            raise HTTPException(400, "travel 需要目标地点")
        action = {"intent": "travel", "type": "travel",
                  "summary": f"前往{target}", "target": target,
                  "end_chat": True, "confirmed": True, "forced": True}
    else:
        action = {"intent": "act", "type": "use" if target else "observe",
                  "summary": f"使用{target}" if target else "观察四周环境",
                  "target": target, "end_chat": False,
                  "forced": True}

    async def _act_events():
        try:
            yield {"type": "action_detect", "action_type": action.get("type", "other"),
                   "summary": action.get("summary", ""), "end_chat": action.get("end_chat", False),
                   "blocked": False}
            applied = _chat.action.apply_action(novel_id, action)
            changed = applied.get("changed", [])
            async for ev in _chat.action.action_scene_stream(novel_id, action, changed):
                yield ev
            if action.get("end_chat"):
                yield {"type": "action_done", "end_chat": True, "action": action,
                       "snapshot": _state_snapshot(applied.get("state") or {})}
                yield {"type": "done"}
            else:
                yield {"type": "action_done", "end_chat": False, "action": action,
                       "snapshot": _state_snapshot(applied.get("state") or {})}
                # 非 end_chat 行动后角色反应（对话继续）
                async for ev in _chat.chat_stream(novel_id, action.get("summary", ""), None):
                    yield ev
        except Exception as e:
            log.error(f"act stream error: {e}")
            yield {"type": "error", "message": f"行动执行失败: {type(e).__name__}"}

    async def act_stream():
        async for data in _sse_with_heartbeat(_act_events()):
            yield data

    return StreamingResponse(act_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
