"""互动模式 UI 截图验证（v3.5 Galgame 风格）"""
import sys, time
sys.path.insert(0, r"C:\Users\Yan Zhao\WorkBuddy\Claw\NovelGenerator")
from playwright.sync_api import sync_playwright

OUT = r"C:\Users\Yan Zhao\WorkBuddy\Claw\NovelGenerator\outputs\interactive_ui_v35.png"

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1100, "height": 800})
    page.goto("http://127.0.0.1:8787", wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(1500)
    # 打开书架第一本有互动的小说
    try:
        page.click("text=替身的告别", timeout=8000)
    except Exception:
        page.click(".shelf-item >> nth=0", timeout=8000)
    page.wait_for_timeout(1200)
    # 进入写作页/详情 → 互动模式 tab
    try:
        page.click("text=互动模式", timeout=6000)
    except Exception:
        page.click("text=🎮", timeout=6000)
    page.wait_for_timeout(3000)
    # 等场景加载完成
    page.wait_for_timeout(8000)
    page.screenshot(path=OUT, full_page=False)
    print("saved:", OUT)
    browser.close()
