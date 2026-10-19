"""Start the local desktop workshop: python run.py [--no-browser] [--port 8787]."""
import argparse
import json
import os
from pathlib import Path
import sys
import threading
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))


def main():
    parser = argparse.ArgumentParser(description="墨局 · 本地小说工坊")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8787")))
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    url = f"http://127.0.0.1:{args.port}"
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url + "/api/health", timeout=1) as response:
            status = json.load(response)
        if status.get("service") == "NovelGenerator":
            print(f"Workshop is already running: {url}")
            if not args.no_browser:
                webbrowser.open(url)
            return
    except (OSError, ValueError):
        pass
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    import uvicorn
    print(f"\n墨局 · 小说工坊\n{url}\n按 Ctrl+C 停止服务\n")
    uvicorn.run("api.server:app", host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
