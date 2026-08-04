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

import json
import logging
import os
import shutil
import time
from typing import Any, Optional

log = logging.getLogger(__name__)

CHECKPOINT_KEEP = 10          # 快照保留份数
FACT_ACTIVE = "active"
FACT_FULFILLED = "fulfilled"
FACT_EXPIRED = "expired"
FACT_BROKEN = "broken"


def _atomic_write_json(path: str, data: Any):
    """原子写 JSON（tmp + rename），Windows 下文件监控器可能短暂持锁 → 重试"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + f".tmp{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    # v3.6.3: Windows 重试——uvicorn --reload 的 WatchFiles 可能瞬间持有目标文件句柄
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt < 4:
                time.sleep(0.02 * (attempt + 1))  # 20ms → 40ms → 60ms → 80ms
            else:
                raise  # 5 次都失败 → 不再吞异常


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


def new_state(novel_id: str, title: str, genre: str, style: str,
              protagonist_name: str = "") -> dict:
    """初始化剧情状态"""
    return {
        "novel_id": novel_id,
        "title": title,
        "genre": genre,
        "style": style,
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

    # ── 路径 ──
    def _base_dir(self, novel_id: str) -> str:
        if novel_id not in self._dir_cache:
            d = os.path.join(self.novels_dir, novel_id, "interactive")
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
        """读取状态（带内存缓存 + 损坏重放修复）"""
        if novel_id in self._state_cache:
            return self._state_cache[novel_id]
        st = _read_json(self._state_path(novel_id), None)
        if st is None and os.path.exists(self._state_path(novel_id)):
            log.warning(f"state.json 损坏，尝试重放修复: {novel_id}")
            st = self._rebuild_from_logs(novel_id)
        if st is not None and not isinstance(st.get("state"), dict):
            log.warning(f"state.json 结构异常，尝试重放修复: {novel_id}")
            st = self._rebuild_from_logs(novel_id)
        # v3.6: 旧存档迁移——世界三支柱补全（幂等）
        if st is not None and "world" not in st:
            try:
                from .world_state import ensure_world
                ensure_world(st)
            except Exception as e:
                log.warning(f"world migration failed: {e}")
        self._state_cache[novel_id] = st
        return st

    def save_state(self, novel_id: str, state: dict):
        """原子写状态 + 更新缓存"""
        state["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _atomic_write_json(self._state_path(novel_id), state)
        self._state_cache[novel_id] = state

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
        """回退到最近一份快照（不删除快照，保留可再回退）"""
        cps = self.list_checkpoints(novel_id)
        if not cps:
            return False
        latest = os.path.join(self._checkpoint_dir(novel_id), cps[0])
        st = _read_json(latest, None)
        if st is None:
            return False
        self.save_state(novel_id, st)
        log.info(f"Rollback {novel_id} -> {cps[0]}")
        return True

    # ── 日志 ──
    def append_scene(self, novel_id: str, scene: dict):
        _append_log(self._scenes_path(novel_id), scene)

    def append_chat(self, novel_id: str, entry: dict):
        _append_log(self._chats_path(novel_id), entry)

    def recent_scenes(self, novel_id: str, n: int = 3) -> list:
        return _read_logs(self._scenes_path(novel_id), n)

    def recent_chats(self, novel_id: str, n: int = 20) -> list:
        return _read_logs(self._chats_path(novel_id), n)

    # ── 音色覆盖 ──
    def get_voice_overrides(self, novel_id: str) -> dict:
        return _read_json(self._voices_path(novel_id), {}) or {}

    def set_voice_override(self, novel_id: str, char_name: str, voice_cfg: dict):
        over = self.get_voice_overrides(novel_id)
        over[char_name] = voice_cfg
        _atomic_write_json(self._voices_path(novel_id), over)

    def clear_voice_overrides(self, novel_id: str):
        _atomic_write_json(self._voices_path(novel_id), {})

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
        """从 scene_logs 重建状态（丢失的 facts 无法还原，但场景链完整）"""
        scenes = _read_logs(self._scenes_path(novel_id))
        if not scenes:
            return None
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
        log.info(f"Rebuilt interactive state from {len(scenes)} scenes: {novel_id}")
        _atomic_write_json(self._state_path(novel_id), st)
        return st

    # ── 重开 ──
    def restart(self, novel_id: str) -> bool:
        """重置互动存档（旧存档复制备份到 interactive-backup-<ts>/）

        注意：不做目录删除/移动（Windows safe-delete 钩子会拦截），
        只做"复制备份 + 覆盖清空"，全程写操作。
        """
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
