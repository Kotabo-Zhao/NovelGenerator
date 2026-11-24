#!/usr/bin/env python3
"""One-command launcher for 墨局 · NovelGenerator v5."""

from __future__ import annotations

import argparse
import os
import threading
import time
import webbrowser

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="启动 墨局 · NovelGenerator v5 创作工坊")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=8787, help="监听端口（默认 8787）")
    parser.add_argument("--data-dir", default="", help="指定 SQLite 与配置数据目录")
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    args = parser.parse_args()

    if args.data_dir:
        os.environ["NOVELGEN_DATA_DIR"] = args.data_dir

    url = f"http://{args.host}:{args.port}"
    if not args.no_browser:
        def _open():
            time.sleep(0.9)
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()

    print(f"墨局 · NovelGenerator v5 已启动 -> {url}")
    uvicorn.run("backend.app:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
