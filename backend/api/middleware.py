"""ASGI limits held until the final response body, including SSE streams."""
from collections import defaultdict, deque
import os
import time

from starlette.responses import JSONResponse

_active_generation_count = 0


def active_generations():
    return _active_generation_count


class RequestLimits:
    def __init__(self, app):
        self.app = app
        self.requests = defaultdict(deque)
        self.active = defaultdict(int)
        self.window = 60
        self.maximum = int(os.getenv("RATE_LIMIT", "180"))
        self.concurrency = int(os.getenv("RATE_LIMIT_GENERATE_CONCURRENT", "3"))
        self.cleaned = time.monotonic()

    async def __call__(self, scope, receive, send):
        global _active_generation_count
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            return await self.app(scope, receive, send)
        ip = (scope.get("client") or ("local", 0))[0]
        path = scope["path"]
        generating = scope["method"] == "POST" and any(
            marker in path for marker in ("generate", "/create", "/chat/send", "/chat/regenerate")
        )
        now = time.monotonic()
        if now - self.cleaned >= self.window:
            for key in list(self.requests):
                if not self.requests[key] or self.requests[key][-1] <= now - self.window:
                    self.requests.pop(key, None)
            self.cleaned = now
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
