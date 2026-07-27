#!/usr/bin/env python3
"""XHS 自动化流水线：生成 → 格式化 → 排期输出

用法:
  # 单次生产
  python tools/xhs_pipeline.py --preset slap_001
  python tools/xhs_pipeline.py --template 爽文_打脸逆袭 --inspiration "自定义灵感"

  # 批量生产（从配置文件）
  python tools/xhs_pipeline.py --batch configs/xhs_batch.json

  # 定时生产（配合 cron / Docker）
  python tools/xhs_pipeline.py --schedule daily --count 3

  # 只格式化已有小说
  python tools/xhs_pipeline.py --format-novel "小说标题"

输出目录结构:
  output/xhs_queue/
    ├── ready/          # 生成完成，待发送
    │   ├── 2026-07-28_001_被退婚后我成了首富.html
    │   └── 2026-07-28_001_被退婚后我成了首富.txt
    ├── sent/           # 已发送归档
    └── schedule.json   # 排期计划
"""

import argparse
import json
import os
import sys
import time
import re
import textwrap
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# ── Config ──
API_BASE = os.environ.get("XHS_API_URL", "http://localhost:8002")
OUTPUT_DIR = Path(os.environ.get("XHS_OUTPUT_DIR", os.path.join(os.path.dirname(__file__), "..", "output", "xhs_queue")))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
(OUTPUT_DIR / "ready").mkdir(exist_ok=True)
(OUTPUT_DIR / "sent").mkdir(exist_ok=True)
SCHEDULE_FILE = OUTPUT_DIR / "schedule.json"

# ── API Helpers ──

def api_get(path: str) -> dict:
    import urllib.request
    url = f"{API_BASE}{path}"
    try:
        with urllib.request.urlopen(url) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"❌ API error ({path}): {e}")
        return {"ok": False, "error": str(e)}


def api_post(path: str, data: dict) -> dict:
    import urllib.request
    url = f"{API_BASE}{path}"
    try:
        req = urllib.request.Request(url, data=json.dumps(data).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"❌ API error ({path}): {e}")
        return {"ok": False, "error": str(e)}


def load_schedule() -> dict:
    if SCHEDULE_FILE.exists():
        with open(SCHEDULE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"posts": [], "last_run": None}


def save_schedule(data: dict):
    with open(SCHEDULE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── Generation ──

def generate_story(template: str, inspiration: str, wait: bool = True) -> Optional[dict]:
    """调用API生成短篇，返回 {novel_id, chapters, titles, total_words}"""
    print(f"📝 Generating: {template} — {inspiration[:50]}…")
    
    if wait:
        # SSE streaming — read events until done
        import urllib.request
        url = f"{API_BASE}/api/xiaohongshu/create"
        req = urllib.request.Request(url, data=json.dumps({
            "template": template, "inspiration": inspiration
        }).encode(), headers={"Content-Type": "application/json"}, method="POST")
        
        result = None
        try:
            with urllib.request.urlopen(req) as r:
                buffer = ""
                for line_bytes in r:
                    line = line_bytes.decode("utf-8").strip()
                    if line.startswith("data: "):
                        try:
                            evt = json.loads(line[6:])
                            if evt.get("type") == "progress":
                                pct = evt.get("pct", 0)
                                bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
                                print(f"\r  [{bar}] {pct}% — {evt.get('message','')}", end="", flush=True)
                            elif evt.get("type") == "done":
                                result = evt
                                print(f"\r  [{'█'*20}] 100% — Done! {result.get('total_words',0)}字")
                        except json.JSONDecodeError:
                            pass
        except Exception as e:
            print(f"\n❌ SSE error: {e}")
            return None
        
        if result and result.get("ok"):
            return {
                "novel_id": result["novel_id"],
                "template": template,
                "chapters": result["chapters"],
                "titles": result["titles"],
                "total_words": result["total_words"],
            }
    else:
        # Fire-and-forget
        resp = api_post("/api/xiaohongshu/create", {"template": template, "inspiration": inspiration})
        if resp.get("ok"):
            print(f"  ✅ Started: {resp.get('novel_id')}")
        return resp
    
    return None


# ── Formatting ──

def format_xhs_html(story: dict, title: str, output_path: str):
    """生成小红书长文格式的 HTML 文件"""
    chapters = story.get("chapters", [])
    
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>{title}</title>
<style>
body {{ font-family: -apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; line-height: 2; color: #333; background: #fff; }}
h1 {{ font-size: 20px; text-align: center; margin-bottom: 5px; color: #e94560; }}
.meta {{ text-align: center; color: #999; font-size: 12px; margin-bottom: 20px; }}
.section {{ margin-bottom: 16px; }}
.section-title {{ font-size: 16px; font-weight: bold; color: #555; border-left: 3px solid #e94560; padding-left: 10px; margin: 20px 0 10px; }}
.section p {{ text-indent: 2em; margin: 8px 0; }}
.divider {{ text-align: center; color: #ccc; margin: 20px 0; font-size: 14px; }}
.footer {{ text-align: center; color: #999; font-size: 12px; margin-top: 30px; padding-top: 15px; border-top: 1px solid #eee; }}
.copy-btn {{ display: block; margin: 20px auto; padding: 10px 30px; background: #e94560; color: #fff; border: none; border-radius: 6px; font-size: 14px; cursor: pointer; }}
</style>
</head>
<body>
<h1>{title}</h1>
<div class="meta">全文约 {story.get('total_words', 0):,} 字 · {len(chapters)} 章</div>
"""
    
    for i, ch in enumerate(chapters):
        text = ch.get("text", "")
        paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
        paragraphs = [p for p in paragraphs if not p.startswith("#")]
        
        ch_num = ch.get("number", i + 1)
        ch_title = ch.get("title", f"第{ch_num}章")
        
        html += '<div class="section">\n'
        html += f'<div class="section-title">{ch_title}</div>\n'
        for p in paragraphs:
            html += f"<p>{p}</p>\n"
        html += '</div>\n'
        
        if i < len(chapters) - 1:
            html += '<div class="divider">· · · 翻页继续 · · ·</div>\n'
    
    html += f"""
<button class="copy-btn" onclick="navigator.clipboard.writeText(document.body.innerText)">📋 一键复制全文</button>
<div class="footer">Generated by NovelGenerator · {datetime.now().strftime('%Y-%m-%d')}</div>
</body>
</html>"""
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    return output_path


def format_xhs_txt(story: dict, title: str, output_path: str):
    """生成纯文本版，适合直接复制到小红书长文编辑器"""
    chapters = story.get("chapters", [])
    
    lines = [title, "=" * len(title), ""]
    
    for i, ch in enumerate(chapters):
        text = ch.get("text", "")
        lines.append(f"【{ch.get('title', f'第{i+1}章')}】")
        lines.append("")
        # Clean up markdown
        for line in text.split("\n"):
            line = line.strip()
            if line and not line.startswith("#"):
                lines.append(line)
        lines.append("")
        
        if i < len(chapters) - 1:
            lines.append("─── 翻页继续 ───")
            lines.append("")
    
    lines.append("")
    lines.append("— 全文完 —")
    lines.append(f"（共约 {story.get('total_words', 0):,} 字）")
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return output_path


def generate_post_text(story: dict, title: str, hook_chars: int = 800) -> str:
    """生成小红书笔记正文（开头试读部分，截断在钩子处）"""
    chapters = story.get("chapters", [])
    if not chapters:
        return ""
    
    # Get the first two chapters or first 800 chars
    text_parts = []
    total = 0
    for ch in chapters[:2]:
        ch_text = ch.get("text", "")
        for line in ch_text.split("\n"):
            line = line.strip()
            if line and not line.startswith("#"):
                text_parts.append(line)
                total += len(line)
                if total >= hook_chars:
                    break
        if total >= hook_chars:
            break
    
    body = "\n\n".join(text_parts)
    
    # Add call to action
    body += "\n\n———\n📖 完整版已上架橱窗，点击主页查看～\n"
    body += f"#{'已完结小说'} #{'短篇完结'} #好剧推荐\n"
    
    return body


# ── Scheduling ──

def plan_schedule(stories: list, days_ahead: int = 7) -> list:
    """为生成的短篇安排发布时间"""
    schedule = load_schedule()
    existing = schedule.get("posts", [])
    
    now = datetime.now()
    plan = []
    
    for i, story in enumerate(stories):
        # Post at 8 PM, one per day
        post_time = now + timedelta(days=len(existing) + i)
        post_time = post_time.replace(hour=20, minute=0, second=0, microsecond=0)
        
        entry = {
            "id": f"{post_time.strftime('%Y-%m-%d')}_{i+1:03d}_{story.get('titles',[''])[0][:20]}",
            "scheduled": post_time.isoformat(),
            "title": story.get("titles", [""])[0],
            "novel_id": story.get("novel_id", ""),
            "template": story.get("template", ""),
            "status": "scheduled",
            "files": story.get("files", []),
        }
        plan.append(entry)
    
    schedule["posts"] = existing + plan
    schedule["last_run"] = now.isoformat()
    save_schedule(schedule)
    
    return plan


# ── CLI ──

def cmd_generate(args):
    """单篇生成"""
    story = generate_story(args.template, args.inspiration, wait=not args.no_wait)
    if story:
        # Format outputs
        title = story["titles"][0] if story["titles"] else args.inspiration[:30]
        date_str = datetime.now().strftime("%Y-%m-%d_%H%M")
        safe_title = re.sub(r'[^\w\s-]', '', title)[:30]
        
        html_path = str(OUTPUT_DIR / "ready" / f"{date_str}_{safe_title}.html")
        txt_path = str(OUTPUT_DIR / "ready" / f"{date_str}_{safe_title}.txt")
        
        format_xhs_html(story, title, html_path)
        format_xhs_txt(story, title, txt_path)
        story["files"] = [html_path, txt_path]
        
        print(f"\n📂 Output:")
        print(f"   HTML: {html_path}")
        print(f"   TXT:  {txt_path}")
        print(f"\n📝 Titles:")
        for i, t in enumerate(story["titles"]):
            print(f"   {i+1}. {t}")
        
        # Also generate post text
        post = generate_post_text(story, title)
        post_path = str(OUTPUT_DIR / "ready" / f"{date_str}_{safe_title}_post.txt")
        with open(post_path, "w", encoding="utf-8") as f:
            f.write(post)
        print(f"   Post: {post_path}")
        
        if args.schedule:
            plan_schedule([story])
            print(f"\n📅 Added to schedule")
        
        return story
    return None


def cmd_batch(args):
    """批量生成"""
    config_path = args.config
    if not os.path.exists(config_path):
        print(f"❌ Config not found: {config_path}")
        return
    
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    
    tasks = config.get("tasks", [])
    print(f"📦 Batch mode: {len(tasks)} stories to generate\n")
    
    stories = []
    for i, task in enumerate(tasks):
        template = task.get("template", "爽文_打脸逆袭")
        inspiration = task.get("inspiration", "")
        preset_id = task.get("preset_id")
        
        if preset_id:
            # Look up preset by ID from API
            presets = api_get("/api/xiaohongshu/presets") or {}
            preset_list = presets.get("presets", [])
            preset = next((p for p in preset_list if p["id"] == preset_id), None)
            if preset:
                template = preset["template"]
                inspiration = preset["inspiration"]
        
        print(f"[{i+1}/{len(tasks)}] ", end="")
        story = generate_story(template, inspiration, wait=not args.no_wait)
        if story:
            # Format
            title = story["titles"][0] if story["titles"] else inspiration[:30]
            date_str = datetime.now().strftime("%Y-%m-%d_%H%M")
            safe_title = re.sub(r'[^\w\s-]', '', title)[:30]
            
            html_path = str(OUTPUT_DIR / "ready" / f"{date_str}_{safe_title}.html")
            txt_path = str(OUTPUT_DIR / "ready" / f"{date_str}_{safe_title}.txt")
            format_xhs_html(story, title, html_path)
            format_xhs_txt(story, title, txt_path)
            story["files"] = [html_path, txt_path]
            stories.append(story)
        time.sleep(args.delay)
    
    if stories and args.schedule:
        plan_schedule(stories)
        print(f"\n📅 {len(stories)} stories added to schedule")
    
    print(f"\n✅ Batch complete: {len(stories)}/{len(tasks)} generated")
    return stories


def cmd_schedule(args):
    """定时生产模式"""
    count = args.count
    presets = api_get("/api/xiaohongshu/presets") or {}
    preset_list = presets.get("presets", [])
    
    if not preset_list:
        print("❌ No presets available")
        return
    
    # Randomly select presets
    import random
    selected = random.sample(preset_list, min(count, len(preset_list)))
    
    stories = []
    for i, preset in enumerate(selected):
        print(f"[{i+1}/{count}] ", end="")
        story = generate_story(preset["template"], preset["inspiration"], wait=True)
        if story:
            title = story["titles"][0] if story["titles"] else preset["label"]
            date_str = datetime.now().strftime("%Y-%m-%d_%H%M")
            safe_title = re.sub(r'[^\w\s-]', '', title)[:30]
            
            html_path = str(OUTPUT_DIR / "ready" / f"{date_str}_{safe_title}.html")
            txt_path = str(OUTPUT_DIR / "ready" / f"{date_str}_{safe_title}.txt")
            format_xhs_html(story, title, html_path)
            format_xhs_txt(story, title, txt_path)
            story["files"] = [html_path, txt_path]
            stories.append(story)
        time.sleep(args.delay)
    
    if stories:
        plan_schedule(stories)
        print(f"\n📅 {len(stories)} stories scheduled")
    
    return stories


def cmd_format(args):
    """格式化已有小说"""
    novel_id = args.format_novel
    
    # Fetch chapters from API
    novel_resp = api_get(f"/api/novels/{novel_id}")
    if not novel_resp.get("ok"):
        print(f"❌ Novel not found: {novel_id}")
        return
    
    novel = novel_resp.get("novel", {})
    chapters = []
    for ch_num in sorted(novel.get("state", {}).get("completed_chapters", [])):
        ch_resp = api_get(f"/api/novels/{novel_id}/chapters/{ch_num}")
        if ch_resp.get("ok"):
            chapters.append({
                "number": ch_num,
                "title": f"第{ch_num}章",
                "text": ch_resp.get("content", ""),
            })
    
    if not chapters:
        print("❌ No chapters found")
        return
    
    # Generate titles
    titles_resp = api_post("/api/xiaohongshu/titles", {"novel_id": novel_id})
    titles = titles_resp.get("titles", [novel.get("title", novel_id)])
    
    story = {
        "novel_id": novel_id,
        "chapters": chapters,
        "titles": titles,
        "total_words": sum(len(c["text"]) for c in chapters),
    }
    
    title = titles[0]
    date_str = datetime.now().strftime("%Y-%m-%d_%H%M")
    safe_title = re.sub(r'[^\w\s-]', '', title)[:30]
    
    html_path = str(OUTPUT_DIR / "ready" / f"{date_str}_{safe_title}.html")
    txt_path = str(OUTPUT_DIR / "ready" / f"{date_str}_{safe_title}.txt")
    format_xhs_html(story, title, html_path)
    format_xhs_txt(story, title, txt_path)
    
    print(f"📂 Formatted:")
    print(f"   HTML: {html_path}")
    print(f"   TXT:  {txt_path}")
    
    post = generate_post_text(story, title)
    post_path = str(OUTPUT_DIR / "ready" / f"{date_str}_{safe_title}_post.txt")
    with open(post_path, "w", encoding="utf-8") as f:
        f.write(post)
    print(f"   Post: {post_path}")


def cmd_status():
    """查看排期状态"""
    schedule = load_schedule()
    posts = schedule.get("posts", [])
    
    print(f"\n📅 XHS Post Schedule")
    print(f"   Last run: {schedule.get('last_run', 'Never')}")
    print(f"   Total posts: {len(posts)}")
    
    ready = list((OUTPUT_DIR / "ready").glob("*"))
    sent = list((OUTPUT_DIR / "sent").glob("*"))
    print(f"   Ready to post: {len(ready)} files")
    print(f"   Sent: {len(sent)} files")
    
    print(f"\n{'─'*60}")
    for p in posts[:10]:
        status_icon = "✅" if p.get("status") == "posted" else "📌"
        print(f"  {status_icon} {p.get('scheduled','?')[:16]} — {p.get('title','?')[:40]}")
    if len(posts) > 10:
        print(f"  ... and {len(posts)-10} more")


def main():
    parser = argparse.ArgumentParser(description="XHS Auto Pipeline")
    sub = parser.add_subparsers(dest="command")
    
    # generate
    gen = sub.add_parser("generate", help="Generate single story")
    gen.add_argument("--template", default="爽文_打脸逆袭")
    gen.add_argument("--inspiration", required=True)
    gen.add_argument("--no-wait", action="store_true")
    gen.add_argument("--schedule", action="store_true")
    
    # batch
    bat = sub.add_parser("batch", help="Batch generate from config")
    bat.add_argument("--config", required=True)
    bat.add_argument("--no-wait", action="store_true")
    bat.add_argument("--schedule", action="store_true")
    bat.add_argument("--delay", type=int, default=5)
    
    # schedule
    sch = sub.add_parser("schedule", help="Auto-generate on schedule")
    sch.add_argument("--count", type=int, default=3)
    sch.add_argument("--delay", type=int, default=5)
    
    # format
    fmt = sub.add_parser("format", help="Format existing novel")
    fmt.add_argument("--format-novel", dest="format_novel", required=True)
    
    # status
    sub.add_parser("status", help="Show schedule status")
    
    args = parser.parse_args()
    
    if args.command == "generate":
        cmd_generate(args)
    elif args.command == "batch":
        cmd_batch(args)
    elif args.command == "schedule":
        cmd_schedule(args)
    elif args.command == "format":
        cmd_format(args)
    elif args.command == "status":
        cmd_status()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
