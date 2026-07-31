#!/usr/bin/env python3
"""
sync_android_core.py — NovelGenerator backend → android python 单向同步

背景：NovelGenerator 后端在 backend/ 和 android/app/src/main/python/ 双份部署。
2026-07-31 起同步范围扩展为:
  - backend/core/*.py            → android python/core/   （核心逻辑，必须一致）
  - backend/api/{server,deps}.py + api/routers/*.py → android python/api/（API 层已统一拆分，同样必须一致）

用法:
  python tools/sync_android_core.py            # 同步到 android
  python tools/sync_android_core.py --check    # 只检查差异，不写入（CI 用，不一致返回 1）
"""

import os
import sys
import shutil
import filecmp
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 需要同步的目录对: (源, 目标, 说明)
SYNC_PAIRS = [
    (ROOT / "backend" / "core", ROOT / "android" / "app" / "src" / "main" / "python" / "core", "core"),
    (ROOT / "backend" / "api", ROOT / "android" / "app" / "src" / "main" / "python" / "api", "api"),
]

# 同步 .py 文件（跳过缓存与平台特有文件）
SKIP_NAMES = {"__pycache__", "server_runner.py", "__init__.py"}


def sync_dir(src: Path, dst: Path, label: str, check_only: bool) -> tuple:
    if not src.is_dir():
        print(f"[ERROR] 源目录不存在: {src}")
        sys.exit(1)
    dst.mkdir(parents=True, exist_ok=True)

    changed, added, removed = [], [], []
    src_files = {p.name for p in src.glob("*.py") if p.name not in SKIP_NAMES}
    dst_files = {p.name for p in dst.glob("*.py") if p.name not in SKIP_NAMES}

    # 新增文件
    for name in sorted(src_files - dst_files):
        added.append(name)
        if not check_only:
            shutil.copy2(src / name, dst / name)

    # 变更文件
    for name in sorted(src_files & dst_files):
        if not filecmp.cmp(src / name, dst / name, shallow=False):
            changed.append(name)
            if not check_only:
                shutil.copy2(src / name, dst / name)

    # 多余文件（android 有但 backend 没有）
    for name in sorted(dst_files - src_files):
        removed.append(name)

    print(f"\n[{label}] {src.name}/ → {dst.name}/")
    print(f"  新增 {len(added)}: {added or '无'}")
    print(f"  更新 {len(changed)}: {changed or '无'}")
    if removed:
        print(f"  ⚠️ android 独有 {len(removed)}: {removed}（未删除，请人工确认是否遗留）")
    return added, changed, removed


def main():
    check_only = "--check" in sys.argv
    print(f"源: {ROOT / 'backend'}")
    print(f"目标: {ROOT / 'android' / 'app' / 'src' / 'main' / 'python'}")

    total_changed = 0
    for src, dst, label in SYNC_PAIRS:
        a, c, r = sync_dir(src, dst, label, check_only)
        total_changed += len(a) + len(c) + len(r)

    if check_only:
        print("\n[CHECK] 仅检查模式，未写入任何文件")
        if total_changed > 0:
            print(f"❌ 双副本不一致（共 {total_changed} 项）— 请先运行 python tools/sync_android_core.py 同步")
            sys.exit(1)
        print("✅ backend 与 android 双副本完全一致")
    else:
        print("\n[SYNC] 同步完成")


if __name__ == "__main__":
    main()
