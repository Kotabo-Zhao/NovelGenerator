"""FeedbackStore — 章节质量反馈的 SQLite 持久化存储

反馈闭环（v2.3.5）：
- 用户对每章 👍/👎 + 一句话理由 → 落库
- 聚合生成"偏好指令"注入 Writer，实现"越写越懂你"
"""
import json
import os
import sqlite3
import threading
import time


class FeedbackStore:
    def __init__(self, novels_dir: str, db_name: str = "feedback.db"):
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
                    """CREATE TABLE IF NOT EXISTS chapter_feedback (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        novel_id TEXT NOT NULL,
                        chapter_num INTEGER NOT NULL,
                        rating INTEGER NOT NULL,      -- 1 赞 / -1 踩 / 0 中性
                        reason TEXT DEFAULT '',
                        created_at REAL NOT NULL)"""
                )
                conn.commit()
            finally:
                conn.close()

    def submit(self, novel_id: str, chapter_num: int, rating: int, reason: str = "") -> bool:
        """提交一条反馈（同章重复提交则覆盖最近一条）"""
        rating = max(-1, min(1, int(rating)))
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "DELETE FROM chapter_feedback WHERE novel_id=? AND chapter_num=?",
                    (novel_id, chapter_num),
                )
                conn.execute(
                    "INSERT INTO chapter_feedback (novel_id, chapter_num, rating, reason, created_at) VALUES (?,?,?,?,?)",
                    (novel_id, chapter_num, rating, reason or "", time.time()),
                )
                conn.commit()
                return True
            except Exception:
                return False
            finally:
                conn.close()

    def list(self, novel_id: str, limit: int = 100) -> list:
        """列出全部反馈（按时间倒序）"""
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT chapter_num, rating, reason, created_at FROM chapter_feedback "
                    "WHERE novel_id=? ORDER BY created_at DESC LIMIT ?",
                    (novel_id, limit),
                ).fetchall()
                return [
                    {"chapter": r[0], "rating": r[1], "reason": r[2], "created_at": r[3]}
                    for r in rows
                ]
            except Exception:
                return []
            finally:
                conn.close()

    def build_preference_instruction(self, novel_id: str, max_recent: int = 20) -> str:
        """聚合最近反馈 → Writer 偏好指令文本

        规则（轻量版）：
        - 好评(1) 的 reason 提取为"应保持"偏好
        - 差评(-1) 的 reason 提取为"应改进"偏好
        - 不足 3 条反馈或全部中性 → 返回空串（不注入）
        """
        items = self.list(novel_id, max_recent)
        if not items:
            return ""
        goods = [i["reason"].strip() for i in items if i["rating"] == 1 and i["reason"].strip()]
        bads = [i["reason"].strip() for i in items if i["rating"] == -1 and i["reason"].strip()]
        if len(goods) + len(bads) < 3:
            return ""
        parts = []
        if goods:
            parts.append("【用户认可的方向·必须保持】\n" + "\n".join(f"- {g[:80]}" for g in goods[:5]))
        if bads:
            parts.append("【用户不满的方向·必须改进】\n" + "\n".join(f"- {b[:80]}" for b in bads[:5]))
        if not parts:
            return ""
        return "## 🧭 用户偏好（基于历史反馈，写作时遵守）\n" + "\n\n".join(parts)
