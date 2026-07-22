"""Atomic file I/O — 防止写入中途崩溃导致文件损坏

用法:
    atomic_write_json(path, data)      # 原子写入 JSON
    safe_read_json(path, default={})   # 安全读取，损坏时自动恢复
"""

import json
import os
import logging
import shutil

log = logging.getLogger(__name__)


def atomic_write_json(path: str, data, indent: int = 2):
    """原子写入 JSON 文件：先写 .tmp → rename，防止写入中途崩溃导致文件损坏"""
    dirname = os.path.dirname(path)
    os.makedirs(dirname, exist_ok=True)
    
    # 写入临时文件（使用固定命名模式避免并发冲突）
    tmp_path = path + ".writing.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
        # 原子 rename（同文件系统下是原子操作）
        os.replace(tmp_path, path)
    except Exception:
        # 清理临时文件
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise


def safe_read_json(path: str, default=None):
    """安全读取 JSON，文件不存在或损坏时返回 default 并自动备份损坏文件"""
    if default is None:
        default = {}
    
    if not os.path.exists(path):
        return default
    
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        # 文件损坏 → 备份损坏文件 + 返回默认值 + 写入修复版本
        backup = path + ".corrupted"
        try:
            shutil.copy2(path, backup)
            log.warning(f"Corrupted file backed up: {backup}")
        except Exception:
            pass
        
        log.error(f"Failed to read {path}: {e}. Restoring to default.")
        
        # 修复：写入有效的默认值
        try:
            atomic_write_json(path, default)
        except Exception:
            pass
        
        return default


def atomic_write_text(path: str, content: str):
    """原子写入文本文件"""
    dirname = os.path.dirname(path)
    os.makedirs(dirname, exist_ok=True)
    
    tmp_path = path + ".writing.tmp"
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
