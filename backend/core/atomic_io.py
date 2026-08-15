"""Atomic file I/O — 防止写入中途崩溃导致文件损坏

用法:
    atomic_write_json(path, data)      # 原子写入 JSON
    safe_read_json(path, default={})   # 安全读取，损坏时自动恢复
"""

import json
import os
import uuid
import logging

log = logging.getLogger(__name__)


def atomic_write_json(path: str, data, indent: int = 2):
    """原子写入 JSON: UUID临时文件避免并发冲突"""
    dirname = os.path.dirname(path)
    os.makedirs(dirname, exist_ok=True)
    
    tmp_path = f"{path}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
        os.replace(tmp_path, path)
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise


def safe_read_json(path: str, default=None):
    """安全读取 JSON，损坏时备份原文件并返回 default（绝不覆写用户数据）。

    损坏的原文件会被原子移到 ``<path>.corrupted.<时间戳>`` 保留，
    由显式修复流程决定是否重建；本函数不写回 default，避免静默覆盖。
    """
    if default is None:
        default = {}
    
    if not os.path.exists(path):
        return default
    
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        import time as _time
        backup = f"{path}.corrupted.{int(_time.time())}"
        try:
            # 原子移走损坏文件：既保留原文，又避免下次读取重复触发。
            os.replace(path, backup)
            log.warning(f"Corrupted file moved aside: {backup}")
        except OSError as be:
            log.error(f"Failed to move corrupted file {path}: {be}")
        log.error(f"Failed to read {path}: {e}. Returning default without overwriting.")
        return default


def atomic_write_text(path: str, content: str):
    """原子写入文本文件 (UUID临时文件)"""
    dirname = os.path.dirname(path)
    os.makedirs(dirname, exist_ok=True)
    
    tmp_path = f"{path}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise
