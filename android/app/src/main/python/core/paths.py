"""Validate storage identifiers before any router touches the filesystem."""
import os


class InvalidNovelPathError(ValueError):
    pass


def novel_path(root: str, novel_id: str) -> str:
    if (not isinstance(novel_id, str) or not novel_id.strip() or len(novel_id) > 200
            or novel_id in (".", "..") or novel_id.endswith((".", " "))
            or any(c in novel_id for c in '<>:"/\\|?*\x00')
            or any(ord(c) < 32 for c in novel_id)):
        raise InvalidNovelPathError("无效的作品 ID")
    base = os.path.realpath(root)
    target = os.path.realpath(os.path.join(base, novel_id))
    if os.path.normcase(os.path.dirname(target)) != os.path.normcase(base):
        raise InvalidNovelPathError("作品路径超出存储目录")
    return target
