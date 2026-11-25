"""In-memory ring-buffer log capture and secret redaction for safe diagnostics export."""
from __future__ import annotations

from collections import deque
import logging
import re
import threading

_RING_LOCK = threading.Lock()
_LOG_RING: deque[str] = deque(maxlen=250)
_INSTALLED = False

_SECRET_PATTERNS = [
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]{8,}", re.IGNORECASE), r"\1***REDACTED***"),
    (re.compile(r"\b(sk-|ghp_|github_pat_)[A-Za-z0-9_\-]{6,}"), r"\1***REDACTED***"),
    (
        re.compile(r'(["\']?(?:api_key|apikey|token|secret|password)["\']?\s*[:=]\s*["\']?)[^"\'\s,}]{4,}', re.IGNORECASE),
        r"\1***REDACTED***",
    ),
]


def redact_secrets(text: str) -> str:
    """Mask API keys, bearer tokens, and passwords in diagnostic log lines."""
    if not text:
        return ""
    out = str(text)
    for pat, repl in _SECRET_PATTERNS:
        out = pat.sub(repl, out)
    return out


class _RingLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = redact_secrets(self.format(record))
            with _RING_LOCK:
                _LOG_RING.append(msg)
        except Exception:
            pass


def install_log_ring() -> None:
    """Attach the bounded ring-buffer handler to the root logger once."""
    global _INSTALLED
    if _INSTALLED:
        return
    handler = _RingLogHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    )
    root = logging.getLogger()
    root.addHandler(handler)
    _INSTALLED = True


def get_recent_logs(limit: int = 200) -> list[str]:
    with _RING_LOCK:
        items = list(_LOG_RING)
    return items[-max(1, min(limit, 250)) :]
