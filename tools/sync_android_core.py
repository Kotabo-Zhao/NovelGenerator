#!/usr/bin/env python3
"""
sync_android_core.py — NovelGenerator backend/core → android python/core 单向同步

背景：NovelGenerator 后端在 backend/ 和 android/app/src/main/python/ 双份部署。
核心逻辑（core/）必须保持一致，server.py 因平台差异（CORS来源、WEB_DIR路径）各自维护。

用法:
  python tools/sync_android_core.py            # 同步 core/ 到 android
  python tools/sync_android_core.py --check    # 只检查差异，不写入
"""

import os
import sys
import shutil
import filecmp
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "backend" / "core"
DST = ROOT / "android" / "app" / "src" / "main" / "python" / "core"

# android 端额外文件（不需要反向同步，保留）
KEEP_LOCAL = {"__pycache__"}


def main():
    check_only = "--check" in sys.argv
    if not SRC.is_dir():
        print(f"[ERROR] 源目录不存在: {SRC}")
        sys.exit(1)
    if not DST.is_dir():
        print(f"[ERROR] 目标目录不存在: {DST}")
        sys.exit(1)

    changed, added, removed = [], [], []
    src_files = {p.name for p in SRC.glob("*.py")}
    dst_files = {p.name for p in DST.glob("*.py")}

    # 新增文件
    for name in sorted(src_files - dst_files):
        added.append(name)
        if not check_only:
            shutil.copy2(SRC / name, DST / name)

    # 变更文件
    for name in sorted(src_files & dst_files):
        if not filecmp.cmp(SRC / name, DST / name, shallow=False):
            changed.append(name)
            if not check_only:
                shutil.copy2(SRC / name, DST / name)

    # 多余文件（android 有但 backend 没有，排除 KEEP_LOCAL）
    for name in sorted(dst_files - src_files):
        if name not in KEEP_LOCAL:
            removed.append(name)

    print(f"源: {SRC}")
    print(f"目标: {DST}")
    print(f"\n新增 {len(added)}: {added or '无'}")
    print(f"更新 {len(changed)}: {changed or '无'}")
    if removed:
        print(f"⚠️ android 独有 {len(removed)}: {removed}（未删除，请人工确认是否遗留）")

    if check_only:
        print("\n[CHECK] 仅检查模式，未写入任何文件")
    else:
        print("\n[SYNC] 同步完成")


if __name__ == "__main__":
    main()
