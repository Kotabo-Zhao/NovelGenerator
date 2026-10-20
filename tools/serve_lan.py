"""Password-protected LAN access to the existing local workspace."""
import argparse
import base64
import ipaddress
import os
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]


class LanGate:
    def __init__(self, app, password):
        self.app = app
        self.authorization = b'Basic ' + base64.b64encode(('novel:' + password).encode())

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'lifespan':
            return await self.app(scope, receive, send)
        authorization = dict(scope.get('headers', [])).get(b'authorization', b'')
        if secrets.compare_digest(authorization, self.authorization):
            return await self.app(scope, receive, send)
        if scope['type'] == 'websocket':
            return await send({'type': 'websocket.close', 'code': 1008})
        from starlette.responses import PlainTextResponse
        response = PlainTextResponse('Enter the LAN username and password.', status_code=401,
            headers={'WWW-Authenticate': 'Basic realm="NovelGenerator LAN", charset="UTF-8"',
                     'Cache-Control': 'no-store'})
        await response(scope, receive, send)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', required=True)
    parser.add_argument('--port', type=int, default=8791)
    args = parser.parse_args()
    address = ipaddress.ip_address(args.host)
    if not address.is_private or address.is_loopback or address.is_unspecified:
        parser.error('--host must be a specific private LAN address')
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT / 'backend'))
    # Existing local data remains owned by the internal app. Every network request
    # passes through LanGate first; do not enable INTERNAL_NON_LOOPBACK.
    os.environ.update(HOST='127.0.0.1', NOVEL_GENERATOR_INTERNAL='true',
        NOVELS_DIR=str(ROOT/'novels'), ROLEPLAY_DIR=str(ROOT/'_roleplay'), WEB_DIR=str(ROOT/'web'))
    from api.server import app
    import uvicorn
    password = os.environ.get('NOVEL_LAN_PASSWORD') or secrets.token_urlsafe(12)
    print(f'LAN_URL=http://{args.host}:{args.port}/', flush=True)
    print('LAN_USERNAME=novel', flush=True)
    print(f'LAN_PASSWORD={password}', flush=True)
    uvicorn.run(LanGate(app, password), host=args.host, port=args.port,
                proxy_headers=False, access_log=False, log_level='info')


if __name__ == '__main__':
    main()
