"""RequirementsStore — 需求拆解结果的 SQLite 持久化存储

替代原先 NovelEngine 进程内 dict（PENDING: needs DB migration for multi-process）。
- SQLite WAL 模式：多进程并发安全（同机多 worker 可并发读，写串行化）
- 原子写入：崩溃不损坏数据
- 接口与 dict 一致：get(novel_id) / set(novel_id, data)
"""
import json
import os
import sqlite3
import threading
import time


class RequirementsStore:
    def __init__(self, novels_dir: str, db_name: str = "requirements.db"):
        self._path = os.path.join(novels_dir, db_name)
        os.makedirs(novels_dir, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS requirements (
                        novel_id TEXT PRIMARY KEY,
                        data TEXT NOT NULL,
                        updated_at REAL NOT NULL)"""
                )
                conn.commit()
            finally:
                conn.close()

    def get(self, novel_id: str) -> dict:
        """读取需求拆解结果，不存在返回 {}（与旧 dict 行为一致）"""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT data FROM requirements WHERE novel_id=?", (novel_id,)
                ).fetchone()
                if not row:
                    return {}
                return json.loads(row[0])
            except Exception:
                return {}
            finally:
                conn.close()

    def set(self, novel_id: str, data: dict):
        """保存需求拆解结果（空数据不写）"""
        if not data:
            return
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO requirements (novel_id, data, updated_at) VALUES (?,?,?)",
                    (novel_id, json.dumps(data, ensure_ascii=False), time.time()),
                )
                conn.commit()
            finally:
                conn.close()
