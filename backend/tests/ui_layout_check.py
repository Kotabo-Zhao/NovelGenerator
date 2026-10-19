"""Measure roleplay page layout overflow at phone/tablet/desktop widths."""
import json

from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8123/roleplay.html?novel=test-novel"

SCRIPT = """() => {
  const rect = sel => {
    const el = document.querySelector(sel);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return {left: Math.round(r.left), right: Math.round(r.right), width: Math.round(r.width),
            scrollW: el.scrollWidth, clientW: el.clientWidth, height: Math.round(r.height)};
  };
  return {
    docScrollW: document.documentElement.scrollWidth,
    docClientW: document.documentElement.clientWidth,
    bodyScrollW: document.body.scrollWidth,
    innerWidth: window.innerWidth,
    topActions: rect('.rp-top-actions'),
    toolbar: rect('.rp-toolbar'),
    composer: rect('.rp-composer-inner'),
    send: rect('.rp-send, .rp-stop'),
    rail: rect('.rp-rail'),
    topbar: rect('.rp-topbar'),
  };
}"""

with sync_playwright() as p:
    browser = p.chromium.launch(channel="msedge", headless=True)
    for width, height in [(390, 844), (360, 780), (768, 900), (1280, 800)]:
        page = browser.new_page(viewport={"width": width, "height": height})
        page.goto(URL, wait_until="load")
        page.wait_for_timeout(1800)
        metrics = page.evaluate(SCRIPT)
        overflow = metrics["docScrollW"] - metrics["docClientW"]
        print(f"--- {width}x{height} overflowX={overflow}px ---")
        print(json.dumps(metrics, ensure_ascii=False))
        if width == 390:
            page.screenshot(path="C:/dev/novelgen-live/rp-mobile-pw.png")
        page.close()
    browser.close()
