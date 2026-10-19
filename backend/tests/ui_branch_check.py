"""Browser E2E check for the miniprogram-branch roleplay mode."""
import json
import os

from playwright.sync_api import sync_playwright

BASE = os.getenv("NOVELGEN_API", "http://127.0.0.1:8126")
OUT = os.getenv("SHOT_DIR", os.path.join(os.path.dirname(__file__), "..", "..", "dist"))

errors = []

with sync_playwright() as p:
    browser = p.chromium.launch(channel="msedge", headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.on("pageerror", lambda err: errors.append(f"pageerror: {err}"))
    page.on("console", lambda msg: errors.append(f"console: {msg.text}")
            if msg.type == "error" else None)

    # 1) main app loads and exposes the new entry
    page.goto(BASE + "/", wait_until="load")
    page.wait_for_timeout(2500)
    body = page.inner_text("body")
    print("main title:", page.title())
    print("main has 角色扮演:", "角色扮演" in body)
    print("main has 互动:", "互动" in body)
    page.screenshot(path=os.path.join(OUT, "branch-main.png"))

    # 2) standalone roleplay page
    page.goto(BASE + "/roleplay.html", wait_until="load")
    page.wait_for_timeout(2000)
    print("roleplay characters:", page.locator(".rp-side-card").count())
    page.locator(".rp-side-card", has_text="苏晚").first.click()
    page.wait_for_timeout(1200)
    page.fill(".rp-composer textarea", "晚上好，测试一下新分支的角色扮演。")
    page.get_by_role("button", name="发送").click()
    page.wait_for_timeout(3000)
    bubbles = page.locator(".rp-bubble").all_inner_texts()
    print("bubbles after send:", json.dumps(bubbles[-3:], ensure_ascii=False))

    page.get_by_role("button", name="🔄 重新生成").click()
    page.wait_for_timeout(2500)
    bubbles = page.locator(".rp-bubble").all_inner_texts()
    print("bubbles after regen:", json.dumps(bubbles[-2:], ensure_ascii=False))
    page.screenshot(path=os.path.join(OUT, "branch-roleplay-desktop.png"))

    # 3) mobile overflow
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(800)
    metrics = page.evaluate(
        "() => ({scrollW: document.documentElement.scrollWidth,"
        " clientW: document.documentElement.clientWidth})"
    )
    print("mobile metrics:", metrics)
    page.screenshot(path=os.path.join(OUT, "branch-roleplay-mobile.png"))
    browser.close()

print("js errors:", errors if errors else "none")
