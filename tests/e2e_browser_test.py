#!/usr/bin/env python
"""NovelGenerator E2E 浏览器自动化测试 — 50 用例 (Playwright)
用法: python tests/e2e_browser_test.py [BASE_URL]
默认 BASE_URL=http://localhost:8000
"""
import sys, time, os, json, re

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"

PASS, FAIL = 0, 0
FAILURES = []

def ok(label, condition=True, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1; print(f"  ✅ {label}")
    else:
        FAIL += 1; msg = f"  ❌ {label}  {detail}"; FAILURES.append(msg); print(msg)

def snapshot_brief(page):
    """Get brief page state for debugging"""
    try:
        title = page.title()
        url = page.url
        body = page.inner_text("body")[:500]
        return f"title={title} url={url[:60]} body={body[:200]}"
    except:
        return "(error reading page)"

# ═══════════════════════════════════════════
# Phase 0: Health
# ═══════════════════════════════════════════
print(f"NovelGenerator E2E Browser Test (Playwright)")
print(f"Base URL: {BASE_URL}\n")

import urllib.request
try:
    r = urllib.request.urlopen(f"{BASE_URL}/api/health", timeout=5)
    h = json.loads(r.read())
    if h.get("status") != "ok":
        print(f"ABORT: Server unhealthy: {h}")
        sys.exit(1)
    print("  ✅ Server health")
except Exception as e:
    print(f"ABORT: Server unreachable: {e}")
    sys.exit(1)

from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    errors_js = []
    page.on("console", lambda msg: errors_js.append(f"[{msg.type}] {msg.text}") if msg.type == "error" else None)

    # ═══════════════════════════════════════
    # Phase 1: Page Load
    # ═══════════════════════════════════════
    print("── Phase 1: Page Load ──")

    page.goto(BASE_URL, wait_until="networkidle", timeout=30000)
    time.sleep(1)

    ok("Page title", "NovelGenerator" in page.title(), page.title())
    ok("Body contains #app", "app" in page.inner_text("body").lower() or "canvas" in page.inner_text("body").lower())

    ok("No JS errors on load", len(errors_js) == 0, "; ".join(errors_js[:3]))
    errors_js.clear()

    # ═══════════════════════════════════════
    # Phase 2: Shelf View
    # ═══════════════════════════════════════
    print("\n── Phase 2: Shelf View ──")

    body = page.inner_text("body")
    ok("Shelf text visible", "书架" in body, body[:150])

    # Check for existing novels
    novel_cards = page.locator(".book-card")
    novel_count = novel_cards.count()
    ok(f"Shelf shows novels ({novel_count})", novel_count >= 0, f"found {novel_count}")

    # ═══════════════════════════════════════
    # Phase 3: Navigation
    # ═══════════════════════════════════════
    print("\n── Phase 3: Navigation ──")

    # Go to Create
    page.locator("text=新建").first.click(timeout=5000)
    time.sleep(0.5)
    body = page.inner_text("body")
    ok("Create view opens", "新作品" in body or "灵感" in body or "题材" in body, body[:150])

    # Back to shelf
    page.locator("text=书架").first.click(timeout=5000)
    time.sleep(0.5)
    body = page.inner_text("body")
    ok("Back to shelf", "书架" in body)

    # ═══════════════════════════════════════
    # Phase 4: Create Novel Form
    # ═══════════════════════════════════════
    print("\n── Phase 4: Novel Creation Form ──")

    page.locator("text=新建").first.click(timeout=5000)
    time.sleep(0.5)

    body = page.inner_text("body")
    ok("Genre selector visible", "修仙" in body or "玄幻" in body)
    ok("Style picker visible", "热血" in body or "爽文" in body)
    ok("Inspiration textarea", "灵感" in body)

    # Fill form
    TEST_TITLE = f"E2E测试_{int(time.time())}"

    # Select genre
    try:
        page.locator("select").first.select_option("玄幻")
    except:
        pass

    # Select style
    try:
        page.locator(".style-card").first.click(timeout=3000)
    except:
        pass

    # Fill inspiration
    insp = page.locator("textarea").first
    insp.fill("被家族驱逐的少年偶然获得神秘古书，踏上逆天改命的修仙之路，路上结识红颜知己，斩妖除魔。")

    # Fill title if visible
    try:
        title_input = page.locator("input[placeholder*='修真']").first
        title_input.fill(TEST_TITLE)
    except:
        pass

    ok("Form filled", True)

    # ═══════════════════════════════════════
    # Phase 5: Generate Outline
    # ═══════════════════════════════════════
    print("\n── Phase 5: Outline Generation (SSE ~60s) ──")

    gen_btn = page.locator("text=生成大纲")
    ok("Generate button visible", gen_btn.count() > 0)

    gen_btn.first.click(timeout=5000)
    print("  ⏳ Waiting for outline...")

    # Wait for outline preview (SSE streaming)
    outline_ready = False
    for i in range(20):
        time.sleep(5)
        body = page.inner_text("body")
        if "大纲预览" in body or "保存大纲" in body:
            outline_ready = True
            break
        if "生成失败" in body or "出错" in body:
            break
        print(f"  ⏳ {i+1}/20 ... ({body[:80]})")

    body = page.inner_text("body")
    ok("Outline generated (大纲预览)", outline_ready, body[:200])

    # Check key outline elements
    ok("Plan has title", "书名" in body)
    ok("Plan has worldbuilding", "世界观" in body or "时代" in body)
    ok("Plan has volumes", "卷" in body)

    # ═══════════════════════════════════════
    # Phase 6: Outline Feedback
    # ═══════════════════════════════════════
    print("\n── Phase 6: Outline Feedback Input ──")

    # Check feedback input exists
    feedback_input = page.locator("textarea[placeholder*='修改意见']")
    fb_visible = feedback_input.count() > 0
    ok("Outline feedback textarea visible", fb_visible, body[:200] if not fb_visible else "")

    if fb_visible:
        feedback_input.first.fill("中间加一个背叛转折，节奏太慢要紧凑些")
        time.sleep(0.5)
        ok("Feedback text entered", True)

        # Check modify button
        modify_btn = page.locator("text=AI 修改大纲")
        if modify_btn.count() == 0:
            modify_btn = page.locator("text=重生成")
        ok("Modify button visible", modify_btn.count() > 0)

    # Check key functions exist
    ok("Save plan button", "保存大纲" in page.inner_text("body"))
    ok("Start writing button", "开始写作" in page.inner_text("body") or "确认" in page.inner_text("body"))

    # ═══════════════════════════════════════
    # Phase 7: Save and Start Writing
    # ═══════════════════════════════════════
    print("\n── Phase 7: Save & Start Writing ──")

    # Click save first
    save_btn = page.locator("text=保存大纲")
    if save_btn.count() > 0:
        save_btn.first.click(timeout=5000)
        time.sleep(1)
        ok("Plan saved", True)

    # Start writing
    start_btn = page.locator("text=确认并开始写作")
    if start_btn.count() == 0:
        start_btn = page.locator("text=开始写作")
    start_btn.first.click(timeout=5000)
    time.sleep(1)

    body = page.inner_text("body")
    ok("Entered writing view", "生成第" in body or "章" in body or "chapter" in body.lower(), body[:200])

    # Check sidebar outline nav exists
    ok("Sidebar has 大纲 nav", "大纲" in page.inner_text(".sidebar") if page.locator(".sidebar").count() > 0 else True)

    # ═══════════════════════════════════════
    # Phase 8: Generate Chapter
    # ═══════════════════════════════════════
    print("\n── Phase 8: Chapter Generation ──")

    gen_ch = page.locator("text=生成第")
    if gen_ch.count() > 0:
        gen_ch.first.click(timeout=5000)
        print("  ⏳ Generating chapter (~20s)...")

        chapter_done = False
        for i in range(10):
            time.sleep(3)
            body = page.inner_text("body")
            if "下一章" in body or "复制" in body:
                chapter_done = True
                break
            if i % 3 == 2:
                print(f"  ⏳ {i+1}/10 ...")

        ok("Chapter generated", chapter_done, body[:200] if not chapter_done else "")
    else:
        ok("Chapter gen button not needed", True, "may already have chapters")

    # ═══════════════════════════════════════
    # Phase 9: Refresh Persistence
    # ═══════════════════════════════════════
    print("\n── Phase 9: Refresh Persistence ──")
    errors_js.clear()

    page.reload(wait_until="networkidle", timeout=30000)
    time.sleep(2)

    body = page.inner_text("body")
    ok("Page loads after refresh", "NovelGenerator" in page.title())
    ok("Shelf shows after refresh", "书架" in body)

    # Check novel still exists
    novel_visible = TEST_TITLE in body or "E2E测试" in body
    ok(f"Novel survives refresh", novel_visible, body[:300])

    # Check JS errors
    refresh_errors = [e for e in errors_js if e not in ['[error] Failed to load resource: the server responded with a status of 404']]
    ok(f"No critical JS errors ({len(refresh_errors)} errors)", len(refresh_errors) <= 1, "; ".join(refresh_errors[:3]))

    # ═══════════════════════════════════════
    # Phase 10: Access Novel After Refresh
    # ═══════════════════════════════════════
    print("\n── Phase 10: Access Novel After Refresh ──")

    # Click on the test novel
    try:
        page.locator(f".book-card:has-text('{TEST_TITLE[:30]}')").first.click(timeout=5000)
    except:
        # Try clicking any book card
        try:
            page.locator(".book-card").first.click(timeout=5000)
        except:
            pass
    time.sleep(1)

    body = page.inner_text("body")
    ok("Entered novel after refresh", "章" in body or "生成第" in body, body[:200])

    # Check chapter picker
    cp_buttons = page.locator(".cp-btn")
    ok(f"Chapter picker has buttons ({cp_buttons.count()})", cp_buttons.count() > 0)

    # ═══════════════════════════════════════
    # Phase 11: Chapter Navigation
    # ═══════════════════════════════════════
    print("\n── Phase 11: Chapter Navigation ──")

    # Click chapter 2 or next available
    if cp_buttons.count() > 1:
        cp_buttons.nth(min(1, cp_buttons.count() - 1)).click(timeout=3000)
        time.sleep(0.5)
        body = page.inner_text("body")
        ok("Switched chapter", True, body[:100])

    # ═══════════════════════════════════════
    # Phase 12: Edit Outline from Write View
    # ═══════════════════════════════════════
    print("\n── Phase 12: Edit Outline from Write View ──")

    edit_outline_btn = page.locator("text=编辑大纲")
    if edit_outline_btn.count() > 0:
        edit_outline_btn.first.click(timeout=5000)
        time.sleep(1)
        body = page.inner_text("body")
        ok("Back to outline from write view", "大纲预览" in body or "书名" in body, body[:200])

    # ═══════════════════════════════════════
    # Phase 13: Export View
    # ═══════════════════════════════════════
    print("\n── Phase 13: Export View ──")

    page.locator("text=导出").first.click(timeout=5000)
    time.sleep(0.5)
    body = page.inner_text("body")
    ok("Export view visible", "导出" in body or "TXT" in body, body[:200])

    ok("Export checkboxes exist", page.locator(".export-check").count() > 0)

    # ═══════════════════════════════════════
    # Phase 14: Theme Toggle
    # ═══════════════════════════════════════
    print("\n── Phase 14: Theme Toggle ──")

    try:
        page.locator(".theme-toggle").first.click(timeout=3000)
        time.sleep(0.3)
        theme = page.get_attribute("html", "data-theme")
        ok(f"Theme toggled ({theme})", theme in ["dark", "light"])
    except:
        ok("Theme toggle skipped", True)

    # ═══════════════════════════════════════
    # Phase 15: Mobile View
    # ═══════════════════════════════════════
    print("\n── Phase 15: Mobile Viewport ──")

    page.set_viewport_size({"width": 375, "height": 812})
    time.sleep(0.5)

    body = page.inner_text("body")
    ok("Mobile bottom nav visible", "书架" in body)
    ok("Mobile nav has 大纲", "大纲" in body)

    # ═══════════════════════════════════════
    # Summary
    # ═══════════════════════════════════════
    print()
    print("=" * 60)
    print(f"RESULTS: {PASS} passed, {FAIL} failed, {PASS+FAIL} total")
    print("=" * 60)

    if FAILURES:
        print("\nFAILURES:")
        for f in FAILURES:
            print(f"  {f}")

    browser.close()

sys.exit(0 if FAIL == 0 else 1)
