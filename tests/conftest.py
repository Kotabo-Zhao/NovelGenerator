"""Keep manual/live scripts out of deterministic pytest collection.

Also raises API rate limits for the whole test run: the shared TestClient IP
accumulates /api calls across all test files (60/min default would 429 flaky).
Must run before any `api.server` import, hence placed at module top.
"""

import os

# 全量测试共享 TestClient IP，提高限流阈值避免 429 抖动
os.environ.setdefault("RATE_LIMIT", "10000")
os.environ.setdefault("AUTH_RATE_LIMIT", "10000")

collect_ignore = [
    "integration_test.py",
    "stress_test.py",
    "e2e_browser_test.py",
    "test_anchor_e2e.py",
    "test_anchor_llm.py",
    "test_anchor_rules.py",
    "test_autoplay.py",
    "test_content_logic.py",
    "test_history.py",
    "test_interactive.py",
    "test_intro.py",
    "test_logic_consistency.py",
    "test_rule_fallback.py",
    "test_status_live.py",
    "test_status_overview.py",
]
