"""Windows desktop host. Code/resources are read-only; user data stays outside."""
from __future__ import annotations

import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import socket
import sys
import threading
import time
import urllib.request


def main():
    # pywebview derives its base URI from argv[0]; keep it valid after chdir.
    sys.argv[0] = str(Path(sys.argv[0]).resolve())
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--smoke-test', type=Path, help='Write a runtime receipt without opening a window')
    parser.add_argument('--no-window', action='store_true')
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    resources = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
    if not getattr(sys, 'frozen', False):
        sys.path.insert(0, str(resources / 'backend'))
    data = (args.data_dir or Path(os.getenv('LOCALAPPDATA', str(Path.home()))) / 'NovelGenerator').resolve()
    data.mkdir(parents=True, exist_ok=True)
    log_dir = data / 'logs'
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, handlers=[RotatingFileHandler(log_dir/'desktop.log', maxBytes=2_000_000, backupCount=3, encoding='utf-8')], force=True)
    # The desktop host binds only loopback and never changes remote-server auth.
    os.environ.update(HOST='127.0.0.1', NOVEL_GENERATOR_INTERNAL='true',
                      NOVELS_DIR=str(data/'novels'), ROLEPLAY_DIR=str(data/'_roleplay'), WEB_DIR=str(resources/'web'))
    os.chdir(data)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', args.port))
        port = sock.getsockname()[1]
    import uvicorn
    from api.server import app
    config = uvicorn.Config(app, host='127.0.0.1', port=port, log_config=None, access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{port}'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        for _ in range(160):
            if server.started:
                break
            if not thread.is_alive():
                raise RuntimeError('本地服务未能启动，请查看 logs/desktop.log')
            time.sleep(.1)
        else:
            raise RuntimeError('本地服务启动超时')
        if args.smoke_test:
            checks = {}
            for path in ['/api/health','/api/novels','/api/roleplay/characters','/','/roleplay.html','/scene.html','/cards.html','/assets/app-shell.js','/assets/cards.js']:
                with opener.open(url+path, timeout=10) as response:
                    checks[path] = {'status': response.status, 'bytes': len(response.read())}
            import webview
            checks['desktop_host'] = {'webview_imported': True, 'frozen': bool(getattr(sys,'frozen',False))}
            args.smoke_test.resolve().write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding='utf-8')
            return
        if args.no_window:
            while thread.is_alive():
                time.sleep(.5)
            return
        import webview
        webview.create_window('墨局 · 小说与角色', url, width=1360, height=900, min_size=(900,620),
                              background_color='#f5f6fa', text_select=True)
        try:
            webview.start(gui='edgechromium', private_mode=False, storage_path=str(data/'webview'))
        except Exception:
            logging.exception('Native window unavailable; using the installed browser')
            import webbrowser
            webbrowser.open(url)
            while thread.is_alive():
                time.sleep(.5)
    finally:
        server.should_exit = True
        thread.join(timeout=8)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        logging.exception('Desktop startup failed')
        raise
