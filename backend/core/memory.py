"""NovelGenerator — NovelMemory: 分层上下文管理 (向后兼容包装)

v2.1: NovelMemory 现在是 SharedMemoryManager 的薄包装层。
保留所有原有方法签名，底层委托给 SharedMemoryManager 的统一缓存 + 乐观锁机制。

使用 SharedMemoryManager 替代直接文件 I/O，获得:
- 内存缓存 (TTL 30s)
- 乐观锁并发安全
- 统一读写接口
"""

import os
import logging
from typing import Optional
from .shared_memory import SharedMemoryManager

log = logging.getLogger(__name__)


class NovelMemory:
    """分层记忆管理器（向后兼容包装）
    
    三层架构（不变）:
    - L1 核心设定: 世界观规则 + 主角档案 (永远注入, ~500 tokens)
    - L2 近期上下文: 最近3章摘要 + 角色状态快照 (~1500 tokens)
    - L3 伏笔追踪: 所有未闭合伏笔 + 相关度检索 (~1000 tokens)
    
    底层: SharedMemoryManager（统一缓存 + 乐观锁）
    """

    def __init__(self, novels_dir: str = None):
        novels_dir = novels_dir or os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "novels"
        )
        self._smm = SharedMemoryManager(novels_dir)
        self.novels_dir = self._smm.novels_dir
    
    def __getattr__(self, name):
        """代理所有未直接定义的方法到 SharedMemoryManager"""
        return getattr(self._smm, name)

    # ── 路径 ──
    def get_novel_dir(self, novel_id: str) -> str:
        return self._smm.get_novel_dir(novel_id)

    # ── L1: 核心设定 ──
    def get_core_context(self, novel_id: str) -> str:
        return self._smm.get_core_context(novel_id)

    # ── L2: 近期上下文 ──
    def get_recent_context(self, novel_id: str, current_chapter: int) -> str:
        chapters_dir = os.path.join(self.get_novel_dir(novel_id), "chapters")
        if not os.path.exists(chapters_dir):
            return ""
        context_parts = []
        for ch_num in range(max(1, current_chapter - 3), current_chapter):
            ch_path = os.path.join(chapters_dir, f"chapter_{ch_num:04d}.md")
            if os.path.exists(ch_path):
                with open(ch_path, "r", encoding="utf-8") as f:
                    content = f.read()
                summary = content[:1000] + ("..." if len(content) > 1000 else "")
                context_parts.append(f"### 第{ch_num}章摘要\n{summary}")
        return "\n\n".join(context_parts)

    # ── L3: 伏笔 ──
    def get_foreshadowing_context(self, novel_id: str, current_chapter: int) -> str:
        return self._smm._build_foreshadowing_context(novel_id, current_chapter)

    # ── 综合 ──
    def build_writer_context(self, novel_id: str, chapter_num: int,
                             chapter_outline: dict) -> str:
        return self._smm.build_writer_context(novel_id, chapter_num, chapter_outline)

    # ── 写操作 ──
    def save_chapter(self, novel_id: str, chapter_num: int, content: str):
        self._smm.save_chapter(novel_id, chapter_num, content)

    def update_foreshadowing(self, novel_id: str, chapter_num: int,
                             planted: list = None, resolved: list = None):
        self._smm.update_foreshadowing(novel_id, chapter_num, planted, resolved)

    def save_novel_state(self, novel_id: str, state: dict):
        self._smm.save_novel_state(novel_id, state)

    def get_novel_state(self, novel_id: str) -> dict:
        return self._smm.get_novel_state(novel_id)

    def _scan_chapters(self, novel_id: str) -> list:
        return self._smm.scan_chapters(novel_id)
