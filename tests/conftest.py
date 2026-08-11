"""Keep manual/live scripts out of deterministic pytest collection."""

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
