"""Browser acceptance checks for roleplay v2 (run with Edge/Chromium installed)."""
from __future__ import annotations

import os

from playwright.sync_api import sync_playwright

BASE = os.getenv("NOVELGEN_API", "http://127.0.0.1:8128")
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT = os.path.join(ROOT, "dist")
os.makedirs(OUT, exist_ok=True)


def main() -> int:
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda err: errors.append(f"pageerror: {err}"))
        page.on("console", lambda msg: errors.append(f"console: {msg.text}")
                if msg.type == "error" else None)

        page.goto(BASE + "/roleplay.html", wait_until="load")
        page.wait_for_timeout(2200)
        page.screenshot(path=os.path.join(OUT, "v2-desktop.png"))

        # commands: roll + narration + action
        ta = page.locator(".rp-composer textarea")
        ta.fill("/roll 1d20+3")
        ta.press("Enter")
        page.wait_for_timeout(1200)
        ta.fill("/nar 夜色更深了，远处传来更鼓。")
        ta.press("Enter")
        page.wait_for_timeout(900)
        ta.fill("/act 我推门进去，同时拔剑")
        ta.press("Enter")
        page.wait_for_timeout(2500)
        body = page.inner_text("body")
        assert "🎲" in body, "roll badge missing"
        assert "夜色更深了" in body, "narration missing"
        assert "推门进去" in body, "action missing"
        print("[PASS] roll / narration / action")

        # slash palette
        ta.fill("/")
        page.wait_for_timeout(400)
        assert page.locator(".rp-palette").is_visible(), "command palette not visible"
        page.screenshot(path=os.path.join(OUT, "v2-palette.png"))
        ta.press("Escape")
        print("[PASS] command palette")

        # light theme
        page.evaluate("localStorage.setItem('theme', 'light')")
        page.reload(wait_until="load")
        page.wait_for_timeout(1600)
        assert page.evaluate("document.documentElement.getAttribute('data-theme')") == "light"
        page.screenshot(path=os.path.join(OUT, "v2-light.png"))
        print("[PASS] light theme")

        # mobile layout
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(700)
        metrics = page.evaluate(
            "() => ({sw: document.documentElement.scrollWidth,"
            " cw: document.documentElement.clientWidth})"
        )
        assert metrics["sw"] <= metrics["cw"], f"horizontal overflow: {metrics}"
        page.screenshot(path=os.path.join(OUT, "v2-mobile.png"))
        print("[PASS] mobile 390px no overflow")

        browser.close()
    if errors:
        print("JS ERRORS:", errors)
        return 1
    print("[PASS] no JS errors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
