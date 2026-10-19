"""Capture pristine roleplay-v2 screenshots (server should be running)."""
from __future__ import annotations

import os

from playwright.sync_api import sync_playwright

BASE = os.getenv("NOVELGEN_API", "http://127.0.0.1:8128")
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT = os.path.join(ROOT, "dist")


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(BASE + "/roleplay.html", wait_until="load")
        page.wait_for_timeout(2200)
        page.screenshot(path=os.path.join(OUT, "v2-desktop-clean.png"))

        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(700)
        page.screenshot(path=os.path.join(OUT, "v2-mobile-clean.png"))

        light = browser.new_page(viewport={"width": 1440, "height": 900})
        light.add_init_script("localStorage.setItem('theme', 'light')")
        light.goto(BASE + "/roleplay.html", wait_until="load")
        light.wait_for_timeout(1800)
        light.screenshot(path=os.path.join(OUT, "v2-light-clean.png"))
        browser.close()
    print("screenshots written")


if __name__ == "__main__":
    main()
