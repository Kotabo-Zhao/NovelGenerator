"""ASGI limits held until the final response body, including SSE streams."""
from collections import defaultdict, deque
import os
import time

from starlette.responses import JSONResponse

_active_generation_count = 0


def client_ip(scope):
    if os.getenv('TRUST_PROXY_HEADERS', 'false').lower() in {'1','true','yes','on'}:
        for name, value in scope.get('headers', []):
            if name.lower() == b'x-forwarded-for':
                first = value.decode('latin-1').split(',',1)[0].strip()
                if first:
                    return first
    return (scope.get('client') or ('unknown', 0))[0]


def active_generations():
    return _active_generation_count


class RequestLimits:
    def __init__(self, app):
        self.app = app
        self.requests = defaultdict(deque)
        self.active = defaultdict(int)
        self.auth_requests = defaultdict(deque)
        self.auth_window = 15*60
        self.auth_maximum = int(os.getenv('AUTH_RATE_LIMIT','10'))
        self.window = 60
        self.maximum = int(os.getenv("RATE_LIMIT", "180"))
        self.concurrency = int(os.getenv("RATE_LIMIT_GENERATE_CONCURRENT", "3"))
        self.cleaned = time.monotonic()

    async def __call__(self, scope, receive, send):
        global _active_generation_count
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            return await self.app(scope, receive, send)
        ip = client_ip(scope)
        path = scope["path"]
        generating = scope["method"] == "POST" and any(
            marker in path for marker in ("generate", "/create", "/chat/send", "/chat/regenerate", "/chat/swipe", "/interactive/")
        )
        now = time.monotonic()
        if now - self.cleaned >= self.window:
            for key in list(self.requests):
                if not self.requests[key] or self.requests[key][-1] <= now - self.window:
                    self.requests.pop(key, None)
            self.cleaned = now
            for key in list(self.auth_requests):
                if not self.auth_requests[key] or self.auth_requests[key][-1] <= now-self.auth_window:
                    self.auth_requests.pop(key,None)
        if path == '/api/auth/wechat-login':
            recent = self.auth_requests[ip]
            while recent and recent[0] <= now-self.auth_window:
                recent.popleft()
            if len(recent) >= self.auth_maximum:
                return await JSONResponse({'detail':'登录尝试过于频繁，请稍后再试'},429,
                                          headers={'Retry-After':str(self.auth_window)})(scope,receive,send)
            recent.append(now)
        if generating and self.active[ip] >= self.concurrency:
            return await JSONResponse({"detail": "已有生成任务正在进行，请稍后重试"}, 429,
                                      headers={"Retry-After": "5"})(scope, receive, send)
        if not generating:
            recent = self.requests[ip]
            while recent and recent[0] <= now - self.window:
                recent.popleft()
            if len(recent) >= self.maximum:
                return await JSONResponse({"detail": "请求过于频繁，请稍后重试"}, 429,
                                          headers={"Retry-After": "60"})(scope, receive, send)
            recent.append(now)
        else:
            self.active[ip] += 1
            _active_generation_count += 1
        try:
            await self.app(scope, receive, send)
        finally:
            if generating:
                _active_generation_count -= 1
                self.active[ip] -= 1
                if not self.active[ip]:
                    self.active.pop(ip, None)


class NovelWorldJobs:
    """Reserve a novel from request setup until the final SSE body is sent."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if (scope.get("type") != "http" or scope.get("method") not in {"POST", "PUT", "DELETE"}
                or not path.startswith("/api/novels/") or "/interactive/" not in path):
            return await self.app(scope, receive, send)
        from .jobs import try_claim_novel, release_novel
        novel_id = path[len("/api/novels/"):].split("/", 1)[0]
        if not try_claim_novel(novel_id):
            return await JSONResponse({"detail": "本书正在处理剧情，请等待完成后再操作"}, 409)(scope, receive, send)
        try:
            await self.app(scope, receive, send)
        finally:
            release_novel(novel_id)
