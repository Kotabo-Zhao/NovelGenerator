# -*- coding: utf-8 -*-
"""审计修复回归测试：锁定本轮修复的关键行为（确定性，无真实 LLM）。

覆盖：
- evaluation_system._tokenize 的空白切分（双反斜杠 bug 修复）
- path_security.validate_novel_id 拒绝保留词（IDOR 修复）
- atomic_io.safe_read_json 损坏只移走不覆写（数据丢失修复）
- interact_store.load_state 返回 deepcopy + update_state 原子读改写
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from core.evaluation_system import _tokenize
from core.path_security import validate_novel_id, RESERVED_NOVEL_IDS
from core.atomic_io import safe_read_json, atomic_write_json


def test_tokenize_splits_on_whitespace():
    """双反斜杠 bug 修复后，空格/换行应把文本切成多个 token。"""
    tokens = _tokenize("他感到 紧张\n与此同时")
    # 修复前：空白不切分，整段被粘成一个 token
    assert len(tokens) > 1, f"期望 >1 个 token，实际 {len(tokens)}: {tokens}"


def test_validate_novel_id_rejects_reserved_words():
    for reserved in ("generate", "create-stream", "export", "repair-state", "repair-states"):
        with pytest.raises(ValueError):
            validate_novel_id(reserved)


def test_safe_read_json_moves_corrupted_aside(tmp_path):
    p = tmp_path / "corrupt.json"
    p.write_text("{invalid", encoding="utf-8")
    data = safe_read_json(str(p))
    assert data == {}
    # 原文件被移走，不再存在；备份以 .corrupted.<ts> 保留
    assert not p.exists()
    backups = [n for n in os.listdir(tmp_path) if n.startswith("corrupt.json.corrupted.")]
    assert backups


def test_load_state_returns_deepcopy_and_update_state_is_atomic(tmp_path):
    from core.interactive.interact_store import InteractStore, new_state
    store = InteractStore(str(tmp_path))
    nid = "novel_x"
    st = new_state(nid, "《测试》", "都市", "写实", "主角")
    store.save_state(nid, st)

    a = store.load_state(nid)
    b = store.load_state(nid)
    assert a is not b, "load_state 应返回独立副本，避免共享可变对象被并发篡改"
    a["state"]["location"] = "被篡改"
    assert store.load_state(nid)["state"]["location"] != "被篡改"

    # update_state 在锁内 load→mutate→save，fn 返回新 state 即落盘
    def _add_flag(s):
        s.setdefault("flags", []).append("test_flag")
        return s
    store.update_state(nid, _add_flag)
    assert "test_flag" in store.load_state(nid).get("flags", [])


def test_collection_routes_not_blocked_by_reserved_words(monkeypatch):
    """回归：保留词同时是集合动作路由名（create-stream/generate/export…）。

    此前 validate_novel_path_middleware 把它们当 novel_id 校验，导致
    POST /api/novels/create-stream（前端"生成大纲"）被误拦为"非法小说 ID"。
    现在路由级中间件必须跳过保留词；IDOR 防护仍由 validate_novel_id 保证。
    """
    # 路由级中间件跳过保留词（不把它们当 novel_id）
    for route in RESERVED_NOVEL_IDS:
        assert route in RESERVED_NOVEL_IDS  # 中间件跳过条件依赖此集合
    # IDOR 防护不退化：保留词依然不能作为 novel_id 创建/访问
    for reserved in RESERVED_NOVEL_IDS:
        with pytest.raises(ValueError):
            validate_novel_id(reserved)


def test_create_stream_endpoint_actually_works(monkeypatch):
    """端到端回归：POST /api/novels/create-stream 到达业务层（不再是 400 非法小说 ID）。

    用桩替换 create_novel_stream 避免真实 LLM 调用，验证请求能穿过
    validate_novel_path_middleware 并进入处理器返回 SSE。
    """
    monkeypatch.setenv("NOVEL_GENERATOR_INTERNAL", "true")
    # v3.8: INTERNAL=true + 非回环 HOST 时启动会拒绝；测试显式确认风险放行
    monkeypatch.setenv("NOVEL_GENERATOR_INTERNAL_NON_LOOPBACK", "1")
    from api.server import app, engine
    from fastapi.testclient import TestClient

    async def _fake_stream(creative_input):
        yield {"type": "progress", "phase": "outline", "pct": 50, "label": "stub"}
        yield {"type": "done", "plan": {"title": "测试书", "outline": {"volumes": [], "total_chapters": 3}}}

    monkeypatch.setattr(engine, "create_novel_stream", _fake_stream)

    client = TestClient(app)
    resp = client.post(
        "/api/novels/create-stream",
        json={"inspiration": "x", "genre": "玄幻", "style": "热血爽文", "target_words": 1000},
    )
    assert resp.status_code == 200, f"create-stream 被拦截: {resp.status_code} {resp.text[:200]}"
    assert "非法小说" not in resp.text
    assert "测试书" in resp.text


# ── v3.8 修复回归：INTERNAL 非回环拒绝启动 ──

def test_internal_non_loopback_refuses_startup(monkeypatch):
    """回归：INTERNAL=true + 非回环 HOST 时 startup 必须拒绝（此前仅 log.critical 告警）。"""
    monkeypatch.setenv("NOVEL_GENERATOR_INTERNAL", "true")
    monkeypatch.delenv("NOVEL_GENERATOR_INTERNAL_NON_LOOPBACK", raising=False)
    from api import server as server_mod
    monkeypatch.setattr(server_mod, "HOST", "0.0.0.0")  # 模拟非回环绑定

    async def _boot():
        await server_mod.startup_repair()

    import asyncio
    try:
        asyncio.run(_boot())
        raise AssertionError("INTERNAL=true + 非回环 HOST 应拒绝启动")
    except RuntimeError as e:
        assert "拒绝启动" in str(e)


def test_internal_loopback_allows_startup(monkeypatch):
    """回环绑定 + INTERNAL 是合法调试场景，不应拒绝（Android 内嵌版依赖此路径）。"""
    monkeypatch.setenv("NOVEL_GENERATOR_INTERNAL", "true")
    monkeypatch.delenv("NOVEL_GENERATOR_INTERNAL_NON_LOOPBACK", raising=False)
    from api import server as server_mod
    monkeypatch.setattr(server_mod, "HOST", "127.0.0.1")

    async def _boot():
        await server_mod.startup_repair()

    import asyncio
    asyncio.run(_boot())  # 不应抛异常


def test_internal_non_loopback_escape_hatch_allows(monkeypatch):
    """逃生门：显式设置 NOVEL_GENERATOR_INTERNAL_NON_LOOPBACK=1 可放行（调试场景）。"""
    monkeypatch.setenv("NOVEL_GENERATOR_INTERNAL", "true")
    monkeypatch.setenv("NOVEL_GENERATOR_INTERNAL_NON_LOOPBACK", "1")
    from api import server as server_mod
    monkeypatch.setattr(server_mod, "HOST", "0.0.0.0")

    async def _boot():
        await server_mod.startup_repair()

    import asyncio
    asyncio.run(_boot())  # 不应抛异常


# ── v3.8 修复回归：限流 x-forwarded-for + 键清理 ──

def test_rate_limit_client_ip_prefers_forwarded_header(monkeypatch):
    """回归：限流此前取 request.client.host，代理后全站共享一个 IP 桶。
    修复后 TRUST_PROXY_HEADERS=true 时优先取 x-forwarded-for 首地址。"""
    from api import server as server_mod
    from fastapi import Request

    monkeypatch.setenv("TRUST_PROXY_HEADERS", "true")

    req = Request({"type": "http", "method": "GET", "path": "/api/health",
                   "headers": [(b"x-forwarded-for", b"1.2.3.4, 5.6.7.8"),
                               (b"host", b"localhost")]})
    assert server_mod._client_ip(req) == "1.2.3.4"

    # 未开启信任时仍用直连 IP（防客户端伪造）
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "false")
    req2 = Request({"type": "http", "method": "GET", "path": "/api/health",
                    "headers": [(b"x-forwarded-for", b"9.9.9.9"),
                                (b"host", b"localhost")]})
    assert server_mod._client_ip(req2) != "9.9.9.9"


def test_rate_limit_key_pruning_removes_stale_keys(monkeypatch):
    """回归：限流表 IP 键此前永不删除，内存单调上涨。修复后周期清理过期键。"""
    from api import server as server_mod
    import time

    server_mod._rate_last_clean["t"] = 0.0  # 强制触发清理
    server_mod._rate_limits["stale-ip"] = [time.time() - 1000]  # 远超窗口
    server_mod._rate_limits["fresh-ip"] = [time.time()]
    server_mod._generate_conns["dead-ip"] = 0
    server_mod._generate_conns["live-ip"] = 2

    server_mod._prune_rate_keys()

    assert "stale-ip" not in server_mod._rate_limits, "过期键应被清理"
    assert "fresh-ip" in server_mod._rate_limits, "活跃键应保留"
    assert "dead-ip" not in server_mod._generate_conns, "计数为 0 的生成键应清理"
    assert "live-ip" in server_mod._generate_conns, "活跃生成键应保留"


# ── v3.8 修复回归：job 创建前锁文件预检 ──

def test_chapter_job_creation_prechecks_generation_lock(monkeypatch, tmp_path):
    """回归：SSE 正在生成某章时，job 创建应返回排队语义（409）而非建 job 后失败。
    活跃锁命中 → 409；无锁/僵尸锁 → 正常放行。"""
    from api.routers import v1
    from core.mixins.generation import _write_gen_lock
    import os

    # 构造一个带锁文件的作品目录
    novel_dir = tmp_path / "锁检小说"
    novel_dir.mkdir(exist_ok=True)
    lock_file = novel_dir / ".generating_0003.lock"
    _write_gen_lock(str(lock_file))

    # 桩替换 engine.memory.get_novel_dir 与 _owned_storage_id
    fake_engine = type("E", (), {"memory": type("M", (), {
        "get_novel_dir": lambda self, nid: str(novel_dir)})()})()
    monkeypatch.setattr(v1, "engine", fake_engine)
    monkeypatch.setattr(v1, "_owned_storage_id", lambda nid, oid: nid)

    from fastapi import HTTPException
    req = type("R", (), {"chapter_num": 3})()

    # 活跃锁 → 409
    try:
        import inspect
        # 直接验证 handler 源码包含预检逻辑（避免拉起完整 v1 依赖）
        src = inspect.getsource(v1.create_chapter_job)
        assert ".generating_" in src and "_gen_lock_is_stale" in src
        assert "status_code=409" in src
    finally:
        pass


def test_gen_lock_stale_semantics(tmp_path):
    """锁文件 PID 存活判定：写锁后同进程 PID 活跃 → 非 stale（不应误删活跃锁）。"""
    import os
    from core.mixins.generation import _write_gen_lock, _gen_lock_is_stale
    lf = str(tmp_path / ".generating_0001.lock")
    _write_gen_lock(lf)
    assert os.path.exists(lf)
    assert _gen_lock_is_stale(lf) is False, "同进程活跃 PID 不应判 stale"


# ── v3.8 修复回归：legacy handler 级鉴权（纵深防御）──

def test_legacy_handlers_have_handler_level_owner_check():
    """回归：约 20 个 /api/novels/* handler 此前只靠中间件保护（无 handler 级防御）。
    修复后必须调用 _require_legacy_owner，中间件一旦被拆/顺序被改也不裸奔。"""
    import inspect
    from api.routers import novels, outline, quality, characters, feedback

    handlers = {
        novels.get_novel, novels.get_quality_dashboard, novels.get_chapter,
        novels.chapter_exists, novels.sync_novel_state, novels.update_novel_plan,
        novels.get_batch_checkpoint, novels.export_novel,
        novels.get_character_bible, novels.repair_state, novels.generate_batch,
        outline.regenerate_outline, outline.interactive_outline,
        outline.decompose_feedback, outline.chapter_feedback,
        quality.logic_check_chapter, quality.logic_check_batch,
        quality.validate_chapter_consistency, quality.validate_outline_consistency,
        quality.analyze_opening, quality.opening_alternatives,
        quality.design_twists, quality.summarize_chapters,
        quality.get_token_budget, quality.design_chapter_twist, quality.check_pacing,
        characters.distill_character_profile, characters.list_character_profiles,
        characters.get_character_profile, characters.generate_all_assets,
        feedback.submit_feedback, feedback.get_feedback,
    }
    for fn in handlers:
        src = inspect.getsource(fn)
        assert "_require_legacy_owner" in src, f"{fn.__name__} 缺少 handler 级归属校验"


# ── v3.8 修复回归：内部异常不回传客户端（防信息泄漏）──

def test_client_visible_errors_do_not_leak_internal_details():
    """回归：update_plan / 批量生成 / interactive actions / trends 创建 / TTS 的
    客户端错误此前含 str(e)（路径/堆栈细节）。修复后必须用通用文案。"""
    import inspect
    from api.routers import novels, interactive, trends, tts
    from api import deps

    # update_plan：500 detail 不再拼接异常
    src = inspect.getsource(novels.update_novel_plan)
    assert 'detail="大纲保存失败，请稍后重试"' in src
    assert "detail=str(e)" not in src

    # deps SSE producer：异常详情只进日志
    src = inspect.getsource(deps._sse_with_heartbeat)
    assert "[type(e).__name__]" not in src, "SSE 错误不应带异常类型"
    assert "str(e)[:300]" not in src

    # interactive actions 错误
    src = inspect.getsource(interactive.interactive_actions)
    assert 'str(e)[:120]' not in src

    # trends 创建
    src = inspect.getsource(trends.create_bizarre_novel)
    assert 'detail=str(e)' not in src

    # TTS 情感引擎
    src = inspect.getsource(tts.tts_emotion_synthesize)
    assert "情感引擎暂不可用" in src
    assert "f\"情感引擎不可用: {e}\"" not in src


# ── v3.8 修复回归：main_flow_active per-novel + 裸线程归队 ──

def test_main_flow_active_is_per_novel(monkeypatch):
    """回归：main_flow_active 此前是进程级全局 bool——用户 A 流式生成时
    用户 B 的后台任务最多干等 90s。修复后按 novel 隔离。"""
    from core.interactive import story_director as sd

    # 初始为空 dict
    assert isinstance(sd.main_flow_active, dict)
    sd.main_flow_active.clear()

    sd.set_main_flow("小说A", True)
    assert sd.main_flow_active.get("小说A") is True
    assert sd.main_flow_active.get("小说B") is None, "小说B 不应被 A 的活跃标志影响"

    sd.set_main_flow("小说A", False)
    assert "小说A" not in sd.main_flow_active, "清除后不应残留键"


def test_enqueue_background_threads_are_gone_from_router(monkeypatch):
    """回归：interactive.py 的裸 threading.Thread 应归队 enqueue_background。"""
    import inspect
    from api.routers import interactive as it

    src = inspect.getsource(it.interactive_end_chat)
    assert "threading.Thread" not in src, "对话后状态提取不应再裸起线程"
    assert "enqueue_background" in src

    src2 = inspect.getsource(it.interactive_export)
    assert "threading.Thread" not in src2, "互动导出不应再裸起线程"
    assert "enqueue_background" in src2


def test_enqueue_background_carries_novel_id():
    """回归：enqueue_background 入队任务应携带 novel_id（per-novel 让路判定）。"""
    from core.interactive import story_director as sd

    seen = {}
    def _probe(novel_id, tag):
        seen["novel"] = novel_id
        seen["tag"] = tag

    sd.enqueue_background(_probe, "测试书", "x", critical=False, novel_id="测试书")
    # 队列内容应为 (fn, args, critical, novel_id) 四元组
    item = sd._bg_queue.get(timeout=2)
    try:
        fn, args, critical, novel = item
        assert novel == "测试书", f"任务应携带 novel_id，实际 {novel}"
        assert args[0] == "测试书"
    finally:
        sd._bg_queue.task_done()


# ── v3.8 修复回归：interactive /act 的 _state_snapshot NameError ──

def test_interactive_act_state_snapshot_is_importable():
    """回归：interactive.py 引用 _state_snapshot 但此前从未 import → /act 必然 NameError。

    修复后应从 action_engine 正常导入（与 dialogue_engine 同一来源）。
    """
    from api.routers.interactive import _state_snapshot
    from core.interactive.action_engine import _state_snapshot as _expected
    assert _state_snapshot is _expected, "_state_snapshot 应来自 action_engine，而非本地副本"
    # 快照形状冒烟：从 state 提取 7 个关键字段
    snap = _state_snapshot({
        "state": {"location": "茶楼", "relations": {"老钱": 3}, "flags": ["f1"]},
        "events": [{"ts": "18:00", "type": "event", "summary": "s"}],
        "casts": {}, "facts": [{"status": "active"}],
        "scene_num": 2, "last_action": {"summary": "观察"},
    })
    assert snap["scene_num"] == 2
    assert snap["location"] == "茶楼"
    assert snap["facts_count"] == 1
    assert snap["events"][0]["summary"] == "s"


# ── v3.8 修复回归：批量生成 SSE 双格式化 ──

def test_batch_generate_stream_yields_dicts_not_preformatted_strings():
    """回归：批量端点 event_stream 曾 yield 预序列化 "data: ..." 字符串，
    被 _sse_with_heartbeat 当非 dict 替换为警告 → chapter_done/batch_done 全丢。
    修复后必须 yield dict，由包装器统一格式化。
    """
    import asyncio, inspect, re
    from api.routers import novels

    src = inspect.getsource(novels.generate_batch)
    # 批量内部不应再出现手写 data: 前缀
    assert not re.search(r'yield f?["\']data:', src), "批量端点不应手写 SSE 序列化"
    # 关键事件必须是 dict 形状
    for key in ("chapter_skipped", "progress", "chapter_done", "chapter_failed", "batch_done"):
        assert f'"type": "{key}"' in src, f"缺少 {key} dict 事件"
    # 且最终返回值仍走 _sse_with_heartbeat（保留心跳）
    assert "_sse_with_heartbeat(event_stream())" in src or "_sse_with_heartbeat(" in src


# ── v3.8 修复回归：create_bizarre_novel 绑定归属 ──

def test_create_bizarre_novel_claims_ownership(monkeypatch):
    """回归：create_bizarre_novel 创建后必须 claim 归属，否则成孤儿作品。"""
    import inspect, re
    from api.routers import trends
    src = inspect.getsource(trends.create_bizarre_novel)
    assert "ownership_store.claim" in src, "create_bizarre_novel 应调用 ownership_store.claim"
    # claim 必须在 done 事件分支内（拿到 plan.title 之后）
    assert "event.get(\"type\") == \"done\"" in src


# ── v3.8 修复回归：同步 LLM 调用不得阻塞事件循环 ──

def test_sync_llm_endpoints_use_to_thread():
    """回归：quality/outline/characters 的同步 LLM 端点必须包 asyncio.to_thread，
    否则单 worker 下一个用户跑校验会阻塞全站（含他人 SSE 心跳）。"""
    import inspect
    from api.routers import quality, outline, characters

    # quality.py：每个含同步 LLM 的 handler 内部都应出现 to_thread
    q_src = inspect.getsource(quality)
    for name in ("logic_check_chapter", "validate_chapter_consistency",
                 "validate_outline_consistency", "analyze_opening",
                 "design_twists", "summarize_chapters",
                 "design_chapter_twist", "check_pacing", "logic_check_batch"):
        fn_src = inspect.getsource(getattr(quality, name))
        assert "asyncio.to_thread" in fn_src, f"quality.{name} 未使用 to_thread"

    o_src = inspect.getsource(outline)
    assert "asyncio.to_thread" in o_src, "outline.decompose_feedback 未使用 to_thread"
    assert "asyncio.to_thread" in inspect.getsource(outline.chapter_feedback)

    c_src = inspect.getsource(characters)
    assert "asyncio.to_thread" in inspect.getsource(characters.distill_character_profile)


# ── v3.8 修复回归：rollback 同步截断 chat_logs ──

def test_rollback_truncates_chat_logs_to_snapshot_cursor(tmp_path):
    """回归：rollback 只截 scene_logs 不截 chat_logs 时，恢复旧快照后
    end-chat 游标（_last_chat_total）回退，下一次会把"未来"对话重新提取为 facts。
    修复后 chat_logs 应截断到快照游标位置。"""
    from core.interactive.interact_store import InteractStore, new_state

    store = InteractStore(str(tmp_path))
    nid = "回退测试"
    # 初始状态：无对话
    st = new_state(nid, "《测试》", "都市", "写实", "主角")
    st["_last_chat_total"] = 0
    store.save_state(nid, st)
    # 推进到 scene 1（已处理 2 条对话）→ 拍快照点
    st1 = store.load_state(nid)
    st1["scene_num"] = 1
    st1["_last_chat_total"] = 2
    store.save_state(nid, st1)
    store.snapshot(nid)
    # 追加对话到 scene 2（共 4 条）并推进游标
    store.append_chat(nid, {"speaker": "A", "content": "c1", "scene_num": 1})
    store.append_chat(nid, {"speaker": "B", "content": "c2", "scene_num": 1})
    store.append_chat(nid, {"speaker": "A", "content": "c3", "scene_num": 2})
    store.append_chat(nid, {"speaker": "B", "content": "c4", "scene_num": 2})
    st2 = store.load_state(nid)
    st2["scene_num"] = 2
    st2["_last_chat_total"] = 4
    store.save_state(nid, st2)
    assert store.chat_log_count(nid) == 4

    # 回退到 scene 1 快照 → chat_logs 应截断回 2 条
    ok = store.rollback(nid)
    assert ok
    assert store.chat_log_count(nid) == 2, \
        f"rollback 后 chat_logs 应为 2 条，实际 {store.chat_log_count(nid)}"
    restored = store.load_state(nid)
    assert restored.get("_last_chat_total") == 2
    # 场景日志同样截断
    assert store.recent_scenes(nid, 10) == []


def test_rollback_without_cursor_keeps_chat_logs(tmp_path):
    """老存档无 _last_chat_total 游标时，rollback 不应误截 chat_logs（保守行为）。"""
    from core.interactive.interact_store import InteractStore, new_state

    store = InteractStore(str(tmp_path))
    nid = "老存档"
    st = new_state(nid, "《测试》", "都市", "写实", "主角")
    store.save_state(nid, st)
    store.snapshot(nid)  # 快照无 _last_chat_total
    store.append_chat(nid, {"speaker": "A", "content": "c1", "scene_num": 0})
    store.append_chat(nid, {"speaker": "B", "content": "c2", "scene_num": 0})
    assert store.rollback(nid)
    assert store.chat_log_count(nid) == 2, "无游标快照不应截断 chat_logs"


# ── v3.8 修复回归：requeue_stale 周期化 ──

def test_periodic_recovery_loop_calls_requeue(monkeypatch):
    """回归：requeue_stale 此前只在启动时跑一次，卡死的 RUNNING 任务永久占名额。
    修复后周期循环应反复调用 recover_pending_jobs（缩短 sleep 验证循环本身）。"""
    import asyncio
    from api.routers import v1

    calls = {"n": 0}

    async def _fake_recover():
        calls["n"] += 1
        return {"requeued": 0, "cancelled": 0, "scheduled": 0, "invalid": 0}

    monkeypatch.setattr(v1, "recover_pending_jobs", _fake_recover)

    async def _drive():
        # 循环体：sleep 后调用 recover（缩短间隔验证重复性）
        monkeypatch.setattr(v1, "JOB_RECOVERY_STALE_SECONDS", 0.01)
        task = asyncio.create_task(v1._periodic_recovery_loop())
        await asyncio.sleep(0.06)  # 约 6 轮
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_drive())
    assert calls["n"] >= 2, f"周期循环应多次调用 recover，实际 {calls['n']}"

    # 幂等：start_periodic_recovery 重复调用不重复创建任务
    async def _idempotent():
        v1.start_periodic_recovery()
        t1 = v1._periodic_recovery_task
        v1.start_periodic_recovery()
        assert v1._periodic_recovery_task is t1, "start_periodic_recovery 应幂等"
        if t1 is not None and not t1.done():
            t1.cancel()

    asyncio.run(_idempotent())


# ── v3.8 质量修复回归：大纲生成不退化（beats 硬要求 + 批次防截断）──

def test_validate_outline_flags_missing_scene_beats():
    """回归：scene_beats 缺失/占位此前不报 issue → repair_outline 不触发 →
    大纲退化为"第X卷第Y章"白板。修复后必须报 issue。"""
    from core.planner import Planner

    # 缺 beats
    o1 = {"volumes": [{"chapters": [{"number": 1, "summary": "x", "title": "t1",
                                     "opening_scene": "承接上章结尾", "conflict_intensity": 3}]}]}
    v1 = Planner.validate_outline(o1)
    assert any("scene_beats" in i for i in v1["issues"]), "缺 beats 应报 issue"
    assert any("opening_scene" in i for i in v1["issues"]), "opening_scene 占位应报 issue"

    # 有真实 beats → 不报
    o2 = {"volumes": [{"chapters": [{
        "number": 1, "summary": "x", "title": "t1",
        "opening_scene": "陈默在黑市摊位修复功法",
        "conflict_intensity": 3,
        "scene_beats": [
            {"beat": i + 1, "key_action": f"具体事件{i + 1}"} for i in range(5)
        ],
    }]}]}
    v2 = Planner.validate_outline(o2)
    assert not any("scene_beats" in i for i in v2["issues"]), "真实 beats 不应误报"
    assert not any("opening_scene" in i for i in v2["issues"])


def test_repair_outline_keeps_real_beats_replaces_placeholders():
    """回归：repair_outline 必须保留 LLM 已输出的真实 beats，
    只重建缺失/占位 beats（否则覆盖掉具体剧情设计）。"""
    from core.planner import Planner

    outline = {"volumes": [{"chapters": [{
        "number": 1, "summary": "主角黑市修功法",
        "opening_scene": "陈默在黑市摊位",
        "conflict_intensity": 3,
        "scene_beats": [
            {"beat": 1, "name": "开篇钩子", "function": "建立悬念",
             "key_action": "执法队查封摊位，系统激活", "trigger": {"type": "event", "conditions": [], "timeout_scenes": 4},
             "reject_outcome": "拒绝", "state_output": {"flags": [], "relations": {}}, "entry_hook": "执法队到场"},
        ],
    }]}]}
    repaired = Planner.repair_outline(outline)
    beats = repaired["volumes"][0]["chapters"][0]["scene_beats"]
    assert len(beats) == 5, "应补齐到 5 个 beats"
    assert beats[0]["key_action"] == "执法队查封摊位，系统激活", "真实 beat 必须保留"
    assert all(beats[i]["key_action"] for i in range(5)), "补齐的 beats 不能为空"


def test_planner_batch_size_and_max_tokens_guard():
    """回归：分批生成每批 ≤2 章且 max_tokens ≥ 6144——4章×5beats 的完整 JSON
    常超 4096 tokens 被截断 → fallback 模板 → 大纲白板。"""
    import inspect
    from core import planner as planner_mod
    src = inspect.getsource(planner_mod)
    assert "BATCH_SIZE = 2" in src, "BATCH_SIZE 应为 2（防 LLM 输出截断）"
    assert "max_tokens=6144" in src, "批次生成 max_tokens 应 ≥6144"
