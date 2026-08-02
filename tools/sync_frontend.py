#!/usr/bin/env python3
"""前端文件同步脚本 — 将 web/ 复制到 backend/ 和 android/ 的目标位置。

用法:
  python tools/sync_frontend.py          # 同步所有前端文件
  python tools/sync_frontend.py --check  # 仅检查是否一致
"""

import os
import sys
import shutil
import hashlib
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()
SOURCE = ROOT / "web"
TARGETS = [
    ROOT / "backend" / "web",
    ROOT / "android" / "app" / "src" / "main" / "python" / "web",
]

# 需要同步的文件（不包括目录和不需要的文件）
SYNC_FILES = [
    "index.html",
    "sw.js",
    "vue.global.prod.js",
    "manifest.json",
    "test-crash.html",
]

# v3.5.23: 空目录残留（public/src 从未被 git 跟踪、无文件、零引用）——移除硬编码检查，
# 否则 CI checkout 后必然报"缺失目录"（本地工作区有、git 仓库没有）
SYNC_DIRS = []


def md5(path: Path) -> str:
    """计算文件MD5"""
    return hashlib.md5(path.read_bytes()).hexdigest()


def check():
    """检查源文件和目标文件是否一致"""
    all_ok = True
    for target in TARGETS:
        for fname in SYNC_FILES:
            src = SOURCE / fname
            dst = target / fname
            if not dst.exists():
                print(f"  ❌ 缺失: {target.name}/{fname}")
                all_ok = False
            elif md5(src) != md5(dst):
                print(f"  ⚠️ 不同步: {target.name}/{fname}")
                all_ok = False
        for dname in SYNC_DIRS:
            dst = target / dname
            if not dst.exists():
                print(f"  ❌ 缺失目录: {target.name}/{dname}")
                all_ok = False
    if all_ok:
        print("  ✅ 所有前端文件同步一致")
    return all_ok


def sync():
    """同步源文件到所有目标位置"""
    for target in TARGETS:
        print(f"\n  → {target.relative_to(ROOT)}")
        target.mkdir(parents=True, exist_ok=True)

        for fname in SYNC_FILES:
            src = SOURCE / fname
            dst = target / fname
            if not src.exists():
                print(f"    ⚠️ 源文件不存在: {fname}")
                continue
            shutil.copy2(src, dst)
            print(f"    ✅ {fname}")

        for dname in SYNC_DIRS:
            src_dir = SOURCE / dname
            dst_dir = target / dname
            if not src_dir.exists():
                continue
            if dst_dir.exists():
                shutil.rmtree(dst_dir)
            shutil.copytree(src_dir, dst_dir)
            print(f"    ✅ {dname}/ (目录)")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="NovelGenerator 前端文件同步")
    parser.add_argument("--check", action="store_true", help="仅检查一致性，不执行同步")
    args = parser.parse_args()

    print("NovelGenerator 前端同步工具")
    print(f"  源目录: {SOURCE.relative_to(ROOT)}")

    if args.check:
        print("\n检查一致性...")
        ok = check()
        sys.exit(0 if ok else 1)
    else:
        print("\n同步中...")
        sync()
        print("\n✅ 同步完成")
        check()
