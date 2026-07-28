#!/usr/bin/env python3
"""HTML → 截图转换工具，用于将精美的 HTML 小说转换为可直接发小红书的图片

用法:
  python tools/screenshot.py output/xhs_queue/ready/xxx.html    # 单文件截屏
  python tools/screenshot.py --all                               # 截屏 ready/ 下所有文件
  python tools/screenshot.py --watch                             # 监听 ready/ 目录自动截屏
"""

import os
import sys
import argparse
import time
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False
    print("⚠️  Playwright not installed. Install: pip install playwright && playwright install chromium")


def screenshot_html(html_path: str, output_dir: str = None):
    """将 HTML 文件截图为 PNG 图片
    
    Args:
        html_path: HTML 文件路径
        output_dir: 输出目录（默认与 HTML 同目录）
    """
    if not HAS_PLAYWRIGHT:
        print("❌ Playwright required for screenshot. Run: pip install playwright && playwright install chromium")
        return None
    
    html_path = Path(html_path)
    if not html_path.exists():
        print(f"❌ File not found: {html_path}")
        return None
    
    output_dir = Path(output_dir) if output_dir else html_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    
    png_path = output_dir / f"{html_path.stem}.png"
    
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1080, "height": 1920})
        
        # Load HTML
        page.goto(f"file:///{html_path.absolute()}")
        page.wait_for_load_state("networkidle")
        
        # Get full page height
        height = page.evaluate("document.body.scrollHeight")
        page.set_viewport_size({"width": 1080, "height": height})
        
        # Screenshot full page
        page.screenshot(path=str(png_path), full_page=True)
        browser.close()
    
    file_size = png_path.stat().st_size / 1024
    print(f"✅ Screenshot: {png_path} ({file_size:.0f} KB, {height}px)")
    return str(png_path)


def screenshot_all(ready_dir: str):
    """截屏 ready/ 目录下所有 HTML 文件"""
    ready = Path(ready_dir)
    html_files = list(ready.glob("*.html"))
    
    if not html_files:
        print(f"📭 No HTML files in {ready}")
        return
    
    print(f"📸 Screenshotting {len(html_files)} files…")
    for i, f in enumerate(html_files):
        print(f"  [{i+1}/{len(html_files)}] {f.name}")
        screenshot_html(str(f))
        time.sleep(1)  # Small delay between screenshots
    
    print(f"✅ Done. Output: {ready}/*.png")


def watch_and_screenshot(ready_dir: str):
    """监听 ready/ 目录，有新文件时自动截屏"""
    ready = Path(ready_dir)
    seen = set()
    
    print(f"👀 Watching {ready} for new HTML files…")
    
    while True:
        html_files = set(ready.glob("*.html"))
        new_files = html_files - seen
        
        for f in new_files:
            print(f"🆕 New: {f.name}")
            png = screenshot_html(str(f))
            if png:
                # Move HTML to sent/ after screenshot
                sent_dir = ready.parent / "sent"
                sent_dir.mkdir(exist_ok=True)
                f.rename(sent_dir / f.name)
                print(f"   📤 Moved to sent/")
        
        seen = html_files
        time.sleep(5)


def main():
    parser = argparse.ArgumentParser(description="HTML → Screenshot for XHS")
    parser.add_argument("file", nargs="?", help="HTML file to screenshot")
    parser.add_argument("--all", action="store_true", help="Screenshot all HTML in ready/")
    parser.add_argument("--watch", action="store_true", help="Watch ready/ for new files")
    parser.add_argument("--dir", default=None, help="Ready directory path")
    
    args = parser.parse_args()
    
    default_dir = os.path.join(os.path.dirname(__file__), "..", "output", "xhs_queue", "ready")
    
    if args.watch:
        watch_and_screenshot(args.dir or default_dir)
    elif args.all:
        screenshot_all(args.dir or default_dir)
    elif args.file:
        screenshot_html(args.file)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
