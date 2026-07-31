"""
NovelGenerator — EPUB 导出模块

将生成的小说导出为标准 EPUB 格式，支持：
- 元数据（书名/作者/封面）
- 章节分页
- 自动目录（NCX + NAV）
- Kindle 兼容

用法:
  python epub_exporter.py <novel_dir> --title "书名" --author "作者"
"""

import json
import os
import sys
import logging
from pathlib import Path
from typing import List, Optional
from datetime import datetime

log = logging.getLogger(__name__)


def export_epub(
    novel_dir: str,
    output_path: str = None,
    title: str = None,
    author: str = "NovelGenerator",
    cover_path: str = None,
) -> str:
    """
    将小说导出为 EPUB 格式。

    Args:
        novel_dir: 小说目录（含 chapters/ 子目录和 novel.json）
        output_path: 输出 EPUB 文件路径（可选，默认在小说目录下）
        title: 书名（可选，默认从 novel.json 读取）
        author: 作者
        cover_path: 封面图片路径

    Returns:
        输出 EPUB 文件路径
    """
    novel_dir = Path(novel_dir).resolve()
    if not novel_dir.exists():
        raise FileNotFoundError(f"小说目录不存在: {novel_dir}")

    # 读取小说元数据
    meta_path = novel_dir / "novel.json"
    if meta_path.exists():
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        if not title:
            title = meta.get("title", meta.get("name", novel_dir.name))
    else:
        meta = {}
        if not title:
            title = novel_dir.name

    # 收集所有章节
    chapters_dir = novel_dir / "chapters"
    chapters = []
    if chapters_dir.exists():
        for f in sorted(chapters_dir.glob("*.md")):
            content = f.read_text(encoding="utf-8")
            ch_title = _extract_title(content, f.stem)
            chapters.append({"title": ch_title, "filename": f.name, "content": content})
        # 也支持 .txt 格式
        for f in sorted(chapters_dir.glob("*.txt")):
            content = f.read_text(encoding="utf-8")
            ch_title = _extract_title(content, f.stem)
            chapters.append({"title": ch_title, "filename": f.name, "content": content})

    if not chapters:
        log.warning("未找到章节文件，尝试读取整体输出")
        # 尝试读取单个 output 文件
        for f in sorted(novel_dir.glob("output*.md")) + sorted(novel_dir.glob("output*.txt")):
            content = f.read_text(encoding="utf-8")
            chapters.append({"title": title, "filename": f.name, "content": content})

    if not chapters:
        raise ValueError(f"未在 {novel_dir} 中找到任何章节文件")

    # 生成输出路径
    if not output_path:
        safe_title = "".join(c for c in title if c.isalnum() or c in " _-").strip()
        output_path = str(novel_dir / f"{safe_title}.epub")

    # 生成 EPUB
    _build_epub(output_path, title, author, chapters, cover_path)
    log.info("EPUB 导出完成: %s (共%d章)", output_path, len(chapters))
    return output_path


def _extract_title(content: str, fallback: str) -> str:
    """从内容中提取章节标题"""
    for line in content.strip().split("\n"):
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
        if line.startswith("## "):
            return line[3:].strip()
    return fallback


def _build_epub(
    output_path: str,
    title: str,
    author: str,
    chapters: List[dict],
    cover_path: Optional[str] = None,
):
    """使用 ebooklib 构建 EPUB 文件"""
    try:
        from ebooklib import epub
    except ImportError:
        log.error("ebooklib 未安装，使用简化版 EPUB 生成")
        return _build_epub_simple(output_path, title, author, chapters)

    book = epub.EpubBook()
    book.set_identifier(f"novel-{hash(title)}-{datetime.now().strftime('%Y%m%d')}")
    book.set_title(title)
    book.set_language("zh")
    book.add_author(author)
    book.add_metadata("DC", "date", datetime.now().strftime("%Y-%m-%d"))
    book.add_metadata("DC", "publisher", "NovelGenerator")

    # 封面
    if cover_path and os.path.exists(cover_path):
        with open(cover_path, "rb") as f:
            book.set_cover("cover.jpg", f.read())

    # 添加 CSS
    style = epub.EpubItem(
        uid="style",
        file_name="style/default.css",
        media_type="text/css",
        content="""
body { font-family: serif; line-height: 1.8; margin: 5%; }
h1 { text-align: center; font-size: 1.5em; margin: 2em 0 1em; }
h2 { text-align: center; font-size: 1.3em; margin: 1.5em 0 1em; }
p { text-indent: 2em; margin: 0.5em 0; }
.title-page { text-align: center; margin-top: 30%; }
.title-page h1 { font-size: 2em; }
.title-page .author { font-size: 1.2em; color: #666; }
""",
    )
    book.add_item(style)

    # 创建标题页
    title_page = epub.EpubHtml(
        title="扉页", file_name="title.xhtml", lang="zh"
    )
    title_page.content = f"""<html><head>
    <link rel="stylesheet" type="text/css" href="style/default.css"/>
    </head><body>
    <div class="title-page"><h1>{title}</h1>
    <p class="author">作者: {author}</p>
    <p style="margin-top: 3em; font-size: 0.8em; color: #999;">由 NovelGenerator AI 创作</p>
    </div></body></html>"""
    book.add_item(title_page)

    # 创建章节
    epub_chapters = [title_page]
    spine = ["nav", title_page]

    for i, ch in enumerate(chapters, 1):
        ch_id = f"ch{i:04d}"
        # 转换 Markdown 为基本 HTML
        html_content = _md_to_html(ch["content"])
        ep_ch = epub.EpubHtml(
            title=ch["title"],
            file_name=f"{ch_id}.xhtml",
            lang="zh",
        )
        ep_ch.content = f"""<html><head>
        <link rel="stylesheet" type="text/css" href="style/default.css"/>
        </head><body>
        <h1>{ch['title']}</h1>
        {html_content}
        </body></html>"""
        book.add_item(ep_ch)
        epub_chapters.append(ep_ch)
        spine.append(ep_ch)

    # 目录（TOC）
    book.toc = [
        epub.Link("title.xhtml", "扉页", "title"),
        (epub.Section("正文"), [epub.Link(f"ch{i+1:04d}.xhtml", ch["title"], f"ch{i+1:04d}") for i, ch in enumerate(chapters)]),
    ]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    # Spine
    book.spine = spine

    # 写入文件
    epub.write_epub(output_path, book)


def _md_to_html(md_text: str) -> str:
    """简单的 Markdown → HTML 转换"""
    lines = md_text.strip().split("\n")
    result = []
    for line in lines:
        line = line.strip()
        if not line:
            result.append("<br/>")
        elif line.startswith("# ") or line.startswith("## ") or line.startswith("### "):
            result.append(f"<p><em>{line.lstrip('#').strip()}</em></p>")
        elif line.startswith("---"):
            result.append("<hr/>")
        else:
            result.append(f"<p>{line}</p>")
    return "\n".join(result)


def _build_epub_simple(output_path: str, title: str, author: str, chapters: List[dict]):
    """无 ebooklib 时的简化版 — 生成 HTML 文件作为回退"""
    html_path = output_path.replace(".epub", ".html")
    chapters_html = ""
    toc_html = '<div class="toc"><h2>目录</h2>'
    for i, ch in enumerate(chapters, 1):
        toc_html += f'<div><a href="#ch{i:04d}">第{i}章 {ch["title"]}</a></div>'
    toc_html += "</div>"

    for i, ch in enumerate(chapters, 1):
        html_content = _md_to_html(ch["content"])
        chapters_html += f'<div id="ch{i:04d}" class="chapter"><h2>第{i}章 {ch["title"]}</h2>{html_content}</div>'

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
body{{font-family:serif;max-width:800px;margin:0 auto;padding:20px;line-height:1.8;color:#333}}
h1{{text-align:center;margin:2em 0 1em}}
.toc{{background:#f5f5f5;padding:20px;border-radius:8px;margin:20px 0}}
.toc a{{color:#0366d6;text-decoration:none}}
.chapter{{margin:40px 0;padding-top:20px;border-top:1px solid #eee}}
.chapter h2{{color:#1a1a2e}}
</style></head>
<body>
<h1>{title}</h1>
<p style="text-align:center;color:#666">作者: {author}</p>
{toc_html}
{chapters_html}
</body></html>"""
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    log.info("简化版 HTML 已生成（未安装 ebooklib）: %s", html_path)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="NovelGenerator EPUB 导出")
    parser.add_argument("novel_dir", help="小说目录路径")
    parser.add_argument("--title", help="书名（默认从 novel.json 读取）")
    parser.add_argument("--author", default="NovelGenerator", help="作者名")
    parser.add_argument("--output", "-o", help="输出路径")
    parser.add_argument("--cover", help="封面图片路径")
    args = parser.parse_args()

    try:
        path = export_epub(args.novel_dir, args.output, args.title, args.author, args.cover)
        print(f"✅ 导出成功: {path}")
    except Exception as e:
        print(f"❌ 导出失败: {e}")
        sys.exit(1)
