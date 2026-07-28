#!/usr/bin/env python3
"""XHS Pipeline Test — 调用 SSE API 生成短篇 → 格式化 HTML → 截图

用法:
  python tools/run_xhs_test.py
"""

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

# ── Config ──
API_BASE = "http://localhost:8002"
SCRIPT_DIR = Path(__file__).parent
OUTPUT_DIR = SCRIPT_DIR / ".." / "output" / "xhs_queue" / "ready"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

TEMPLATE = "爽文_打脸逆袭"
INSPIRATION = "被退婚后我成了首富，三年后回来收购前未婚夫的公司"


def call_sse_api(template: str, inspiration: str) -> dict:
    """调用 SSE API，收集所有事件返回完整结果"""
    url = f"{API_BASE}/api/xiaohongshu/create"
    data = json.dumps({"template": template, "inspiration": inspiration}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")

    print(f"📡 Connecting to SSE API: {template}")
    result = {
        "chapters": [],
        "titles": [],
        "novel_id": "",
        "total_words": 0,
        "ok": False,
    }
    current_chapter = {"number": 0, "title": "", "text": ""}

    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            buffer = ""
            pct = 0
            for line_bytes in r:
                line = line_bytes.decode("utf-8").strip()
                if not line.startswith("data: "):
                    continue
                try:
                    evt = json.loads(line[6:])
                    etype = evt.get("type", "")
                    if etype == "start":
                        print(f"  🎬 开始生成 | 模板: {evt.get('label')} | {evt.get('total_chapters')}章")
                    elif etype == "progress":
                        pct = evt.get("pct", pct)
                        msg = evt.get("message", "")
                        bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
                        print(f"  [{bar}] {pct}% {msg}", flush=True)
                    elif etype == "novel_created":
                        result["novel_id"] = evt.get("novel_id", "")
                        print(f"  📖 小说创建: {evt.get('title', '')}")
                    elif etype == "chapter_start":
                        if current_chapter["text"]:
                            result["chapters"].append(current_chapter)
                            result["total_words"] += len(current_chapter["text"])
                        current_chapter = {
                            "number": evt.get("number", 0),
                            "title": evt.get("title", ""),
                            "text": "",
                        }
                        print(f"  📝 第{evt.get('number')}章: {evt.get('title')} ({evt.get('function')})")
                    elif etype == "chunk":
                        current_chapter["text"] += evt.get("text", "")
                        # Print a dot every 50 chunks
                        if len(current_chapter["text"]) % 500 < 50:
                            print(".", end="", flush=True)
                    elif etype == "chapter_done":
                        if current_chapter["text"]:
                            result["chapters"].append(current_chapter)
                            result["total_words"] += len(current_chapter["text"])
                        print(f"\n  ✅ 第{evt.get('number')}章完成 | {len(current_chapter['text'])}字")
                        result["titles"] = evt.get("titles", result["titles"])
                        current_chapter = {"number": 0, "title": "", "text": ""}
                    elif etype == "done":
                        if current_chapter["text"]:
                            result["chapters"].append(current_chapter)
                            result["total_words"] += len(current_chapter["text"])
                        result["ok"] = True
                        result["titles"] = evt.get("titles", result["titles"])
                        result["template"] = template
                        print(f"\n  🎉 完成! {result['total_words']}字, {len(result['chapters'])}章, {len(result.get('titles',[]))}个标题")
                    elif etype == "error":
                        print(f"\n  ❌ 错误: {evt.get('message', '')}")
                except json.JSONDecodeError:
                    pass
    except Exception as e:
        print(f"\n  ❌ 连接错误: {e}")
        return result

    return result


def format_html(result: dict, title: str, output_path: str):
    """生成精美截图级 HTML"""
    chapters = result.get("chapters", [])
    template_label = result.get("template", TEMPLATE)

    # Generate summary blurb
    summary_lines = []
    for ch in chapters[:2]:
        t = ch.get("text", "")
        for line in t.split("\n"):
            line = line.strip()
            if line and not line.startswith("#") and len(line) > 15:
                summary_lines.append(line[:80] + "...")
                if len(summary_lines) >= 5:
                    break
        if len(summary_lines) >= 5:
            break
    blurb = "\n                ".join(summary_lines[:5]) if summary_lines else "精彩不容错过..."

    # Tag map
    tag_map = {
        "爽文_打脸逆袭": "逆袭 打脸 爽文",
        "虐文_追妻火葬场": "虐文 追妻 虐恋",
        "世情_家庭反转": "家庭 反转 世情",
        "甜宠_高糖轻虐": "甜宠 总裁 高糖",
    }
    tags = tag_map.get(template_label, "小说 推荐")

    # Render chapters HTML
    chapter_cards = ""
    chapter_colors = ["#e94560", "#f05454", "#ffd700", "#22c55e"]
    chapter_emojis = ["🔥", "⚡", "💎", "🏆"]
    for i, ch in enumerate(chapters):
        text = ch.get("text", "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        # Format paragraphs
        paragraphs = ""
        for para in text.split("\n"):
            para = para.strip()
            if para:
                if para.startswith("#"):
                    paragraphs += f'<h3 style="color:{chapter_colors[i%4]};margin-top:24px;">{para.lstrip("# ")}</h3>\n'
                else:
                    paragraphs += f'<p>{para}</p>\n'
        chapter_cards += f"""
      <div class="chapter-card">
        <div class="chapter-header">
          <span class="chapter-badge" style="background:{chapter_colors[i%4]};">{chapter_emojis[i%4]} 第{ch.get('number', '')}章</span>
          <span class="chapter-title">{ch.get('title', '')}</span>
          <span class="chapter-words">{len(text)}字</span>
        </div>
        <div class="chapter-body">{paragraphs}</div>
      </div>"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=1080">
<title>{title}</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{
  font-family: 'PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;
  background: linear-gradient(180deg, #0a0a1a 0%, #13132b 40%, #1a1a3e 100%);
  min-height:100vh; color:#d4d4d8; line-height:2;
  width:1080px;
}}
.cover {{
  width:1080px; margin:0 auto;
  background: linear-gradient(135deg, #e94560 0%, #c23152 50%, #8b1e3f 100%);
  padding:100px 80px 80px; text-align:center; position:relative; overflow:hidden;
}}
.cover::before {{
  content:''; position:absolute; top:-120px; right:-80px;
  width:500px; height:500px;
  background:radial-gradient(circle, rgba(255,255,255,0.08) 0%, transparent 70%);
  border-radius:50%;
}}
.cover::after {{
  content:''; position:absolute; bottom:-60px; left:-40px;
  width:300px; height:300px;
  background:radial-gradient(circle, rgba(255,255,255,0.04) 0%, transparent 70%);
  border-radius:50%;
}}
.cover .badge {{
  display:inline-block; background:rgba(255,255,255,0.15);
  color:rgba(255,255,255,0.9); padding:8px 24px; border-radius:20px;
  font-size:16px; margin-bottom:24px; backdrop-filter:blur(10px);
}}
.cover h1 {{
  font-size:52px; font-weight:900; color:#fff; margin-bottom:16px;
  text-shadow:0 4px 20px rgba(0,0,0,0.3); letter-spacing:2px;
}}
.cover .subtitle {{
  font-size:20px; color:rgba(255,255,255,0.75); margin-bottom:24px;
}}
.cover .tags {{
  display:flex; gap:12px; justify-content:center; flex-wrap:wrap;
}}
.cover .tag {{
  background:rgba(255,255,255,0.12); color:rgba(255,255,255,0.85);
  padding:6px 18px; border-radius:16px; font-size:15px;
  border:1px solid rgba(255,255,255,0.2);
}}

.summary-card {{
  width:960px; margin:40px auto; background:rgba(255,255,255,0.04);
  border:1px solid rgba(255,255,255,0.06); border-radius:16px; padding:40px;
}}
.summary-card h2 {{
  color:#e94560; font-size:24px; margin-bottom:20px; display:flex; align-items:center; gap:8px;
}}
.summary-card .blurb {{
  color:#a0a0b8; font-size:16px; line-height:2.2;
}}

.chapter-card {{
  width:960px; margin:30px auto; background:rgba(255,255,255,0.025);
  border:1px solid rgba(255,255,255,0.05); border-radius:16px;
  overflow:hidden;
}}
.chapter-header {{
  display:flex; align-items:center; gap:16px; padding:20px 40px;
  background:rgba(255,255,255,0.03); border-bottom:1px solid rgba(255,255,255,0.05);
}}
.chapter-badge {{
  padding:6px 16px; border-radius:12px; color:#fff; font-size:14px; font-weight:700;
}}
.chapter-title {{ flex:1; font-size:22px; font-weight:700; color:#e4e4e7; }}
.chapter-words {{ color:#71717a; font-size:14px; }}
.chapter-body {{ padding:30px 40px; }}
.chapter-body p {{ margin-bottom:16px; font-size:17px; color:#c4c4d0; text-indent:2em; }}
.chapter-body h3 {{ font-size:20px; font-weight:700; margin-bottom:16px; }}

.footer {{
  width:1080px; margin:0 auto; padding:60px 80px; text-align:center;
  color:#52525b; font-size:14px;
}}
</style>
</head>
<body>
<div class="cover">
  <div class="badge">📱 小红书爆款短篇</div>
  <h1>{title}</h1>
  <div class="subtitle">被退婚的那天，全城都在看笑话——三年后，轮到我笑了</div>
  <div class="tags">
    {' '.join(f'<span class="tag">#{t.strip()}</span>' for t in tags.split())}
  </div>
</div>

<div class="summary-card">
  <h2>📋 故事速览</h2>
  <div class="blurb">{blurb}</div>
</div>

{chapter_cards}

<div class="footer">
  Generated by NovelGenerator · XHS Pipeline · 仅供预览
</div>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  📄 HTML 已生成: {output_path}")


def main():
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    slug = "被退婚后我成了首富"

    print("=" * 60)
    print("🚀 XHS Pipeline Test")
    print("=" * 60)

    # Step 1: 调用 SSE API 生成
    print("\n[Step 1] 调用 SSE API 生成短篇...")
    result = call_sse_api(TEMPLATE, INSPIRATION)

    if not result.get("ok"):
        print("\n❌ 生成失败!")
        if result.get("chapters"):
            print(f"  但收集到了 {len(result['chapters'])} 章, 尝试继续...")
        else:
            return 1

    print(f"\n✅ 生成完成:")
    print(f"  小说ID: {result.get('novel_id','N/A')}")
    print(f"  总字数: {result.get('total_words',0)}")
    print(f"  章节数: {len(result.get('chapters',[]))}")
    print(f"  标题备选: {result.get('titles',[])}")

    # Step 2: 格式化 HTML
    print(f"\n[Step 2] 格式化 HTML...")
    title = result.get("titles", [f"被退婚后我成了首富"])[0] if result.get("titles") else "被退婚后我成了首富"
    # Sanitize filename
    safe_title = title.replace("/", "_").replace("\\", "_").replace(":", "_").replace("？","").replace("！","")[:30]
    html_path = OUTPUT_DIR / f"{timestamp}_{safe_title}.html"
    format_html(result, title, str(html_path))

    # Step 3: 生成截图
    print(f"\n[Step 3] 生成截图...")
    png_path = OUTPUT_DIR / f"{timestamp}_{safe_title}.png"

    # Use Playwright to screenshot
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1080, "height": 1920})
            page.goto(f"file:///{html_path.as_posix()}", wait_until="networkidle", timeout=30000)
            # Wait a bit for rendering
            page.wait_for_timeout(1000)
            # Get full page height
            full_height = page.evaluate("document.body.scrollHeight")
            page.set_viewport_size({"width": 1080, "height": full_height})
            page.screenshot(path=str(png_path), full_page=True)
            browser.close()
            print(f"  🖼️ 截图已保存: {png_path}")
    except Exception as e:
        print(f"  ⚠️ 截图失败: {e}")
        print(f"  📄 HTML 仍然可用: {html_path}")

    print(f"\n{'='*60}")
    print(f"✅ 管线测试完成!")
    print(f"  HTML: {html_path}")
    print(f"  PNG:  {png_path}")
    print(f"{'='*60}")


if __name__ == "__main__":
    sys.exit(main())
