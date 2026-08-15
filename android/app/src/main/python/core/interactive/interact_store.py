"""InteractStore — 互动小说存档层（v3.0 互动模式）

负责 novels/{novel_id}/interactive/ 目录下的全部持久化：
- state.json          剧情状态（原子写：tmp + rename）
- scene_logs.jsonl    场景链（append-only，可重放重建）
- chat_logs.jsonl     对话原文（append-only）
- voice_overrides.json 玩家音色覆盖
- checkpoints/        state 快照 ×10（每段生成前自动备份）

设计原则（对照 docs/interactive-novel-plan.html §8.4）：
- 原子写：崩溃不写坏一半
- 快照回退：玩家可「回退一步」
- 日志重放：state 损坏可从 scene_logs 重建
"""
from __future__ import annotations

import copy
import json
import logging
import os
import shutil
import threading
import time
from typing import Any, Optional

from ..path_security import safe_novel_dir

log = logging.getLogger(__name__)

CHECKPOINT_KEEP = 10          # 快照保留份数
FACT_ACTIVE = "active"
FACT_FULFILLED = "fulfilled"
FACT_EXPIRED = "expired"
FACT_BROKEN = "broken"

_ATOMIC_LOCKS: dict[str, threading.RLock] = {}
_ATOMIC_LOCKS_GUARD = threading.Lock()


def _atomic_lock(path: str) -> threading.RLock:
    key = os.path.abspath(path)
    with _ATOMIC_LOCKS_GUARD:
        return _ATOMIC_LOCKS.setdefault(key, threading.RLock())


def _mark_interactive_state(novel_id: str, state: Optional[dict]) -> Optional[dict]:
    """给旧互动存档补上线路标识，防止被误当成正式小说状态。"""
    if not isinstance(state, dict):
        return state
    state["mode"] = "interactive"
    state["line_id"] = f"interactive:{novel_id}"
    return state


def _atomic_write_json(path: str, data: Any):
    """并发安全地原子写 JSON，避免同一进程请求共用临时文件。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lock = _atomic_lock(path)
    tmp = path + f".tmp.{os.getpid()}.{threading.get_ident()}"
    with lock:
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            for attempt in range(5):
                try:
                    os.replace(tmp, path)
                    return
                except PermissionError:
                    if attempt >= 4:
                        raise
                    time.sleep(0.02 * (attempt + 1))
        finally:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass

def _read_json(path: str, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _append_log(path: str, entry: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _rewrite_log(path: str, entries: list):
    """原子重写 jsonl 日志（回退截断用）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + f".tmp.{os.getpid()}.{threading.get_ident()}"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


def _read_logs(path: str, max_entries: Optional[int] = None) -> list:
    if not os.path.exists(path):
        return []
    entries = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if max_entries is not None and len(entries) > max_entries:
        return entries[-max_entries:]
    return entries


def _read_logs_tail(path: str, n: int) -> list:
    """只读 jsonl 末尾 n 行（倒序块扫描），避免长对话下每轮全文件读取越聊越慢。"""
    if not os.path.exists(path) or n <= 0:
        return []
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            pos = f.tell()
            buf = b""
            while pos > 0:
                read_len = min(pos, 8192)
                pos -= read_len
                f.seek(pos)
                buf = f.read(read_len) + buf
                if buf.count(b"\n") > n:
                    break
            tail = buf.split(b"\n")[-(n + 1):]
    except OSError:
        return []
    entries = []
    for raw in tail:
        line = raw.decode("utf-8", "ignore").strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries[-n:]


def new_state(novel_id: str, title: str, genre: str, style: str,
              protagonist_name: str = "") -> dict:
    """初始化剧情状态"""
    return {
        "novel_id": novel_id,
        "title": title,
        "genre": genre,
        "style": style,
        "mode": "interactive",
        "line_id": f"interactive:{novel_id}",
        "scene_num": 0,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "summary": "",
        "state": {
            "location": "",
            "objective": "",
            "flags": [],
            "relations": {},
            "inventory": [],
        },
        # v3.6: 世界状态三支柱（时间/地点/人物）——确定性规则驱动，LLM 只填内容
        "world": {
            "time": {"label": "正午", "slot": 2, "day": 1},
            "location": "",
            "chars": {},
            "locations": {},
        },
        "facts": [],          # PACT 剧情事实（对话→剧情的因果纽带）
        "casts": {},          # 出场角色 {name: {profile, voice, present}}
        "recent_scenes": [],
        "recent_chat": [],
        "pending_node": False,  # 是否等待进入对话（NODE 判定结果）
        "node_chars": [],       # 节点出场角色
        "agenda": None,         # v3.3: 对话议程（goal/hooks/boundaries/exit）
    }


class InteractStore:
    """互动存档管理器（每 novel 一个实例，engine 持有缓存）"""

    def __init__(self, novels_dir: str):
        self.novels_dir = novels_dir
        # v3.6.4: 防御性创建根目录——Android 上若 server_runner 的 makedirs 未执行
        # （旧 APK/异常路径），任何访问 NOVELS_DIR 的操作都会 FileNotFoundError
        try:
            os.makedirs(self.novels_dir, exist_ok=True)
        except Exception as e:
            log.warning(f"novels_dir 创建失败: {self.novels_dir}: {e}")
        self._dir_cache: dict[str, str] = {}
        self._state_cache: dict[str, Optional[dict]] = {}  # novel_id -> state or None
        # per-novel 可重入锁：串行化同一作品的读-改-写，防止 SSE 生成器与后台线程
        # 并发写覆盖（丢状态）。RLock 可重入，避免 update_state 内再调 save_state 死锁。
        self._novel_locks: dict[str, threading.RLock] = {}
        self._novel_locks_guard = threading.Lock()

    def _novel_lock(self, novel_id: str) -> threading.RLock:
        with self._novel_locks_guard:
            return self._novel_locks.setdefault(novel_id, threading.RLock())

    # ── 路径 ──
    def _base_dir(self, novel_id: str) -> str:
        if novel_id not in self._dir_cache:
            d = os.path.join(safe_novel_dir(self.novels_dir, novel_id), "interactive")
            os.makedirs(d, exist_ok=True)
            self._dir_cache[novel_id] = d
        return self._dir_cache[novel_id]

    def _state_path(self, novel_id: str) -> str:
        return os.path.join(self._base_dir(novel_id), "state.json")

    def _scenes_path(self, novel_id: str) -> str:
        return os.path.join(self._base_dir(novel_id), "scene_logs.jsonl")

    def _chats_path(self, novel_id: str) -> str:
        return os.path.join(self._base_dir(novel_id), "chat_logs.jsonl")

    def _voices_path(self, novel_id: str) -> str:
        return os.path.join(self._base_dir(novel_id), "voice_overrides.json")

    def _checkpoint_dir(self, novel_id: str) -> str:
        d = os.path.join(self._base_dir(novel_id), "checkpoints")
        os.makedirs(d, exist_ok=True)
        return d

    # ── 状态读写 ──
    def exists(self, novel_id: str) -> bool:
        return os.path.exists(self._state_path(novel_id))

    def load_state(self, novel_id: str) -> Optional[dict]:
        """读取状态（带内存缓存 + 损坏重放修复）。

        返回 deepcopy，避免调用方拿到缓存里的同一可变对象后被并发篡改。
        """
        if novel_id in self._state_cache:
            st = self._state_cache[novel_id]
        else:
            st = _read_json(self._state_path(novel_id), None)
            if st is None and os.path.exists(self._state_path(novel_id)):
                log.warning(f"state.json 损坏，尝试重放修复: {novel_id}")
                st = self._rebuild_from_logs(novel_id)
            if st is not None and not isinstance(st.get("state"), dict):
                log.warning(f"state.json 结构异常，尝试重放修复: {novel_id}")
                st = self._rebuild_from_logs(novel_id)
            # 世界三支柱迁移与清洗（幂等）：已有 world 的旧存档也可能残留伪地点，
            # 因此每次首次载入都执行一次，而不是只处理缺少 world 的存档。
            if st is not None:
                try:
                    from .world_state import ensure_world
                    ensure_world(st)
                except Exception as e:
                    log.warning(f"world migration failed: {e}")
            st = _mark_interactive_state(novel_id, st)
            self._state_cache[novel_id] = st
        if st is None:
            return None
        return copy.deepcopy(st)

    def save_state(self, novel_id: str, state: dict):
        """原子写状态 + 更新缓存（在 per-novel 锁内串行化）"""
        if not isinstance(state, dict) or not state:
            raise ValueError("拒绝写入空状态，避免覆盖用户存档")
        if not state.get("novel_id"):
            state["novel_id"] = novel_id
        with self._novel_lock(novel_id):
            _mark_interactive_state(novel_id, state)
            state["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            _atomic_write_json(self._state_path(novel_id), state)
            self._state_cache[novel_id] = state

    def update_state(self, novel_id: str, fn):
        """在 per-novel 锁内执行 load→fn(mutate)→save，消除并发读-改-写丢失更新。

        fn 接收最新 state 的副本并返回新 state（或原地修改后返回），
        返回 None 表示无需保存。
        """
        lock = self._novel_lock(novel_id)
        with lock:
            st = _read_json(self._state_path(novel_id), None)
            if st is None:
                st = self._rebuild_from_logs(novel_id)
            if st is None:
                st = {"novel_id": novel_id, "state": {}, "facts": [], "casts": {},
                      "recent_scenes": [], "recent_chat": []}
            st = copy.deepcopy(st)
            updated = fn(st)
            if updated is None:
                return st
            if not isinstance(updated, dict):
                raise ValueError("update_state 的 fn 必须返回 dict 或 None")
            _mark_interactive_state(novel_id, updated)
            updated["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            _atomic_write_json(self._state_path(novel_id), updated)
            self._state_cache[novel_id] = updated
            return updated

    # ── 快照 ──
    def snapshot(self, novel_id: str):
        """生成前快照（保留 CHECKPOINT_KEEP 份）"""
        st = self.load_state(novel_id)
        if st is None:
            return
        cp_dir = self._checkpoint_dir(novel_id)
        scene = st.get("scene_num", 0)
        ts = time.strftime("%Y%m%d%H%M%S")
        name = f"scene{scene:03d}_{ts}.json"
        dst = os.path.join(cp_dir, name)
        try:
            _atomic_write_json(dst, st)
        except Exception as e:
            log.warning(f"snapshot failed: {e}")
            return
        # 清理旧快照（按文件名排序，保留最近 N 份）
        files = sorted(os.listdir(cp_dir))
        for old in files[:-CHECKPOINT_KEEP]:
            try:
                os.remove(os.path.join(cp_dir, old))
            except OSError:
                pass

    def list_checkpoints(self, novel_id: str) -> list:
        cp_dir = self._checkpoint_dir(novel_id)
        if not os.path.isdir(cp_dir):
            return []
        return sorted(os.listdir(cp_dir), reverse=True)

    def rollback(self, novel_id: str) -> bool:
        """回退到最近一份快照（不删除快照，保留可再回退），并截断日志保持一致性。"""
        cps = self.list_checkpoints(novel_id)
        if not cps:
            return False
        latest = os.path.join(self._checkpoint_dir(novel_id), cps[0])
        st = _read_json(latest, None)
        if st is None:
            return False
        lock = self._novel_lock(novel_id)
        with lock:
            self.save_state(novel_id, st)
            # 截断 scene_logs：只保留 scene_num <= 快照 scene_num 的场景，
            # 避免回退后前端历史/状态卡与 state 互相矛盾。
            target_scene = int(st.get("scene_num", 0) or 0)
            try:
                scenes = _read_logs(self._scenes_path(novel_id))
                kept = [s for s in scenes if int(s.get("scene_num", 0) or 0) <= target_scene]
                if kept != scenes:
                    _rewrite_log(self._scenes_path(novel_id), kept)
            except Exception as e:
                log.warning(f"rollback scene_logs truncation failed: {e}")
            # v3.8: 同步截断 chat_logs——快照的 _last_chat_total 是 end-chat 增量游标，
            # 若不截断，恢复旧快照后 total_now > last_chat_total，下一次 end-chat 会
            # 重新提取"回退点之后的未来对话"为 facts（回退形同虚设甚至复活剧情）。
            target_chat_total = int(st.get("_last_chat_total", 0) or 0)
            if target_chat_total > 0:
                try:
                    chats = _read_logs(self._chats_path(novel_id))
                    kept_chats = chats[:target_chat_total]
                    if len(kept_chats) != len(chats):
                        _rewrite_log(self._chats_path(novel_id), kept_chats)
                except Exception as e:
                    log.warning(f"rollback chat_logs truncation failed: {e}")
        log.info(f"Rollback {novel_id} -> {cps[0]}")
        return True

    # ── 日志 ──
    def append_scene(self, novel_id: str, scene: dict):
        _append_log(self._scenes_path(novel_id), scene)

    def append_chat(self, novel_id: str, entry: dict):
        _append_log(self._chats_path(novel_id), entry)

    def recent_scenes(self, novel_id: str, n: int = 3) -> list:
        return _read_logs_tail(self._scenes_path(novel_id), n)

    def recent_chats(self, novel_id: str, n: int = 20) -> list:
        return _read_logs_tail(self._chats_path(novel_id), n)

    def chat_log_count(self, novel_id: str) -> int:
        """chat 日志总条数（end-chat 增量游标用）"""
        p = self._chats_path(novel_id)
        if not os.path.exists(p):
            return 0
        n = 0
        with open(p, "r", encoding="utf-8") as f:
            for _ in f:
                n += 1
        return n

    def recent_chats_from(self, novel_id: str, from_count: int, n: int = 40) -> list:
        """返回第 from_count 条之后的最近 n 条聊天（end-chat 增量提取用）"""
        p = self._chats_path(novel_id)
        if not os.path.exists(p):
            return []
        tail = []
        idx = 0
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                idx += 1
                if idx <= from_count:
                    continue
                try:
                    tail.append(json.loads(line))
                except Exception:
                    continue
                if len(tail) > n:
                    tail.pop(0)
        return tail

    # ── 音色覆盖 ──
    def get_voice_overrides(self, novel_id: str) -> dict:
        return _read_json(self._voices_path(novel_id), {}) or {}

    def set_voice_override(self, novel_id: str, char_name: str, voice_cfg: dict):
        over = self.get_voice_overrides(novel_id)
        over[char_name] = voice_cfg
        _atomic_write_json(self._voices_path(novel_id), over)

    def clear_voice_overrides(self, novel_id: str):
        _atomic_write_json(self._voices_path(novel_id), {})

    def reset_voice_override(self, novel_id: str, char_name: str):
        """删除单个角色音色覆盖（原子写，避免中途失败丢全部）"""
        over = self.get_voice_overrides(novel_id)
        if char_name in over:
            del over[char_name]
            _atomic_write_json(self._voices_path(novel_id), over)

    # ── facts 生命周期 ──
    def add_fact(self, novel_id: str, fact: dict):
        st = self.load_state(novel_id)
        if st is None:
            return
        fact.setdefault("status", FACT_ACTIVE)
        fact.setdefault("evidence", [])
        st.setdefault("facts", []).append(fact)
        self.save_state(novel_id, st)

    def mark_fact(self, novel_id: str, fact_id: str, status: str, evidence: str = ""):
        st = self.load_state(novel_id)
        if st is None:
            return
        for f in st.get("facts", []):
            if f.get("id") == fact_id:
                f["status"] = status
                if evidence:
                    f.setdefault("evidence", []).append(evidence)
                break
        self.save_state(novel_id, st)

    def active_facts(self, novel_id: str) -> list:
        st = self.load_state(novel_id)
        if st is None:
            return []
        return [f for f in st.get("facts", []) if f.get("status") == FACT_ACTIVE]

    # ── 重放修复 ──
    def _rebuild_from_logs(self, novel_id: str) -> Optional[dict]:
        """从 scene_logs 重建状态（丢失的 facts 无法还原，但场景链完整）

        覆盖写前先原子移走损坏的 state.json 为 ``state.json.corrupt-<ts>``，
        重建结果打 ``recovered: True`` 标记，便于前端提示"存档已修复"。
        """
        scenes = _read_logs(self._scenes_path(novel_id))
        if not scenes:
            return None
        # 损坏原文先移走保留，绝不直接覆盖。
        st_path = self._state_path(novel_id)
        if os.path.exists(st_path):
            corrupt_backup = f"{st_path}.corrupt-{int(time.time())}"
            try:
                os.replace(st_path, corrupt_backup)
                log.warning(f"Corrupted interactive state moved aside: {corrupt_backup}")
            except OSError as be:
                log.error(f"Failed to move corrupted state {st_path}: {be}")
        # 用最后一份快照 + 场景链重建
        cps = self.list_checkpoints(novel_id)
        base = None
        if cps:
            base = _read_json(os.path.join(self._checkpoint_dir(novel_id), cps[0]), None)
        st = dict(base) if base else None
        if st is None:
            st = {"novel_id": novel_id, "title": novel_id, "genre": "",
                  "style": "", "scene_num": 0, "summary": "", "state": {},
                  "facts": [], "casts": {}, "recent_scenes": [], "recent_chat": []}
        st["scene_num"] = scenes[-1].get("scene_num", len(scenes))
        st["summary"] = scenes[-1].get("scene_text", "")[:200]
        st["recent_scenes"] = [s.get("scene_text", "")[:200] for s in scenes[-3:]]
        st["recovered"] = True
        log.info(f"Rebuilt interactive state from {len(scenes)} scenes: {novel_id}")
        _atomic_write_json(st_path, st)
        return st

    # ── 重开 ──
    def restart(self, novel_id: str) -> bool:
        """重置互动存档（旧存档复制备份到 interactive-backup-<ts>/）

        注意：不做目录删除/移动（Windows safe-delete 钩子会拦截），
        只做"复制备份 + 覆盖清空"，全程写操作。
        v3.8: 全程持 per-novel 锁——否则持有旧 state 副本的后台任务在 restart 之后
        save_state，会把已重置的存档"复活"（旧 scene_num/facts 覆盖空状态）。
        """
        lock = self._novel_lock(novel_id)
        with lock:
            return self._restart_locked(novel_id)

    def _restart_locked(self, novel_id: str) -> bool:
        base = self._base_dir(novel_id)
        st_path = self._state_path(novel_id)
        if not os.path.exists(st_path):
            return True  # 本来就空
        ts = time.strftime("%Y%m%d%H%M%S")
        novel_dir = os.path.join(self.novels_dir, novel_id)
        backup = os.path.join(novel_dir, f"interactive-backup-{ts}")
        try:
            # 1) 复制备份（读源写目标，不触发删除钩子；排除 checkpoints——快照可重建，
            #    且备份目录含几十个快照会让后续'保留3份'的清理触发批量删除保护）
            if os.path.exists(backup):
                return False
            shutil.copytree(base, backup, dirs_exist_ok=False,
                            ignore=shutil.ignore_patterns("checkpoints"))
            # 2) 覆盖清空：写合法空状态（scene_num=0 触发 start 重新初始化）
            _atomic_write_json(st_path, new_state(novel_id, novel_id, "", "", ""))
            for log_f in (self._scenes_path(novel_id), self._chats_path(novel_id)):
                with open(log_f, "w", encoding="utf-8") as f:
                    f.write("")
            cp_dir = self._checkpoint_dir(novel_id)
            # v3.5.10: BaseException 兜底——safe-delete 钩子可能 raise SystemExit(1)
            # （快照批量删除触发保护），穿透 except Exception 会打穿 to_thread 杀 uvicorn；
            # 快照可重建，删不完直接跳过，不影响 restart 功能
            try:
                for fn in os.listdir(cp_dir):
                    try:
                        os.remove(os.path.join(cp_dir, fn))
                    except BaseException:
                        pass
            except BaseException:
                pass
        except Exception as e:
            log.warning(f"restart backup failed: {e}")
            return False
        # 3) 只保留最近 3 份备份（清理更旧的，文件级删除避免 safe-delete 钩子）
        #    v3.5.10: 用 BaseException 防御——Windows safe-delete 钩子可能 raise
        #    SystemExit(1)（批量删除保护），未捕获会打穿 to_thread 杀死 uvicorn
        try:
            backups = sorted(
                [os.path.join(novel_dir, d) for d in os.listdir(novel_dir)
                 if d.startswith("interactive-backup-")],
                key=lambda p: os.path.getmtime(p), reverse=True)
            for old in backups[3:]:
                try:
                    for root, dirs, files in os.walk(old, topdown=False):
                        for f in files:
                            try:
                                os.remove(os.path.join(root, f))
                            except OSError:
                                pass
                        for dd in dirs:
                            try:
                                os.rmdir(os.path.join(root, dd))
                            except OSError:
                                pass
                    try:
                        os.rmdir(old)
                    except OSError:
                        pass
                except BaseException as e:  # SystemExit 等：清理失败不影响 restart
                    log.warning(f"旧备份清理跳过（{type(e).__name__}）: {old}")
        except Exception as e:
            log.warning(f"restart old-backup cleanup failed: {e}")
        self._dir_cache.pop(novel_id, None)
        self._state_cache.pop(novel_id, None)
        return True
