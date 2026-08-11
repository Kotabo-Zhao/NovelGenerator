"""安全的作品 ID 与文件路径工具。

所有来自 HTTP、任务消息或用户输入的作品 ID 都必须先经过这里，
再参与本地文件系统路径拼接。该模块不依赖 FastAPI，便于核心层和
最小化回归测试复用。
"""
from __future__ import annotations

import ntpath
import os
import re


MAX_NOVEL_ID_LENGTH = 200
_INVALID_ID_CHARS = set('<>"/\\|?*:')
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")


def validate_novel_id(novel_id: str) -> str:
    """校验并返回可作为单级目录名使用的作品 ID。

    这里不做“只允许某种语言字符”的限制，以兼容已有中文标题、空格
    和常见标点；安全边界只允许单级目录名，拒绝绝对路径、分隔符、
    Windows 驱动器/保留名、控制字符和路径遍历序列。
    """
    if not isinstance(novel_id, str) or not novel_id.strip():
        raise ValueError("小说 ID 不能为空")
    if len(novel_id) > MAX_NOVEL_ID_LENGTH:
        raise ValueError(f"小说 ID 过长（最多 {MAX_NOVEL_ID_LENGTH} 字符）")
    if _CONTROL_CHAR_RE.search(novel_id):
        raise ValueError("小说 ID 包含控制字符")
    if any(char in _INVALID_ID_CHARS for char in novel_id):
        raise ValueError("小说 ID 包含非法路径字符")
    if ".." in novel_id or os.path.isabs(novel_id) or ntpath.isabs(novel_id):
        raise ValueError("小说 ID 包含非法路径序列")

    # Windows 会忽略文件名末尾的点/空格，并将这些名称映射到保留设备。
    trimmed = novel_id.rstrip(" .")
    if not trimmed:
        raise ValueError("小说 ID 不能只包含点或空格")
    if trimmed != novel_id:
        raise ValueError("小说 ID 不能以点或空格结尾")
    base_name = trimmed.split(".", 1)[0].upper()
    if base_name in _WINDOWS_RESERVED_NAMES:
        raise ValueError("小说 ID 使用了 Windows 保留名称")

    return novel_id


def _safe_join(root: str, novel_id: str, relative_path: str = "") -> str:
    """将内部相对路径安全地拼接到作品目录下。"""
    validate_novel_id(novel_id)
    if not isinstance(relative_path, str) or not relative_path:
        raise ValueError("相对路径不能为空")
    if "\x00" in relative_path or ntpath.isabs(relative_path) or os.path.isabs(relative_path):
        raise ValueError("相对路径必须是相对路径")

    root_path = os.path.realpath(os.path.abspath(root))
    novel_root = os.path.realpath(os.path.join(root_path, novel_id))
    try:
        novel_inside_root = os.path.commonpath((root_path, novel_root)) == root_path
    except ValueError:
        novel_inside_root = False
    if not novel_inside_root:
        raise ValueError("作品目录超出存储根目录")
    candidate = os.path.realpath(os.path.abspath(os.path.join(novel_root, relative_path)))
    try:
        # 文件必须留在该作品目录内，而不是只留在所有作品共用的 root 内。
        inside_root = os.path.commonpath((novel_root, candidate)) == novel_root
    except ValueError:
        # Windows 不同盘符的路径无法比较 commonpath，应视为越界。
        inside_root = False
    if not inside_root:
        raise ValueError("路径超出作品存储根目录")
    return candidate


def safe_novel_dir(root: str, novel_id: str) -> str:
    """返回作品目录，并保证目录位于 root 下。"""
    return _safe_join(root, novel_id, ".")


def safe_novel_file(root: str, novel_id: str, relative_path: str) -> str:
    """返回作品目录下的文件路径，并拒绝路径越界。"""
    return _safe_join(root, novel_id, relative_path)
