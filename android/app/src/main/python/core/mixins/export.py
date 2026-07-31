"""NovelEngine ExportMixin — 导出 — TXT / EPUB

由 tools/split_engine.py 从 engine.py 自动拆分。
依赖 NovelEngine 提供的 self.client/self.model/self.memory 等属性。
"""
import os
from typing import AsyncGenerator, Optional, AsyncIterator




class ExportMixin:
    def _export_epub(self, title: str, plan: dict, chapters_dir: str, chapters: list) -> tuple:
        """生成 EPUB 电子书"""
        try:
            from ebooklib import epub
        except ImportError:
            return None, "EPUB 导出需要 ebooklib: pip install ebooklib"
        
        book = epub.EpubBook()
        book.set_identifier(f"novelgen-{title}")
        book.set_title(title)
        book.set_language("zh-CN")
        
        author = plan.get("characters", {}).get("protagonist", {}).get("name", "AI Writer")
        book.add_author(author)
        
        # 样式
        style = epub.EpubItem(
            uid="style",
            file_name="style/default.css",
            media_type="text/css",
            content="body{font-family:serif;line-height:1.8;margin:2em}p{text-indent:2em;margin:.5em 0}h1{text-align:center;margin:2em 0}h2{font-size:1.2em;margin:1em 0}",
        )
        book.add_item(style)
        
        spine = ["nav"]
        toc = []
        
        # 书名页
        intro = epub.EpubHtml(title="书名页", file_name="intro.xhtml", lang="zh-CN")
        intro.content = f"""<html><head><link rel="stylesheet" href="style/default.css"/></head>
        <body><h1>{title}</h1>
        <p style="text-align:center">题材: {plan.get('genre','')} | 风格: {plan.get('style','')}</p>
        </body></html>"""
        book.add_item(intro)
        spine.append(intro)
        toc.append(epub.Link("intro.xhtml", "书名页", "intro"))
        
        # 逐章
        for ch_file in chapters:
            try:
                with open(os.path.join(chapters_dir, ch_file), "r", encoding="utf-8") as f:
                    content = f.read()
            except (IOError, UnicodeDecodeError) as e:
                log.warning(f"Failed to read {ch_file} for EPUB: {e}")
                continue
            ch_num = int(ch_file.split("_")[1].split(".")[0]) if "_" in ch_file else 0
            ch_title = f"第{ch_num}章"
            
            c = epub.EpubHtml(title=ch_title, file_name=f"ch{ch_num:04d}.xhtml", lang="zh-CN")
            html_content = content.replace("\n\n", "</p><p>").replace("\n", "<br/>")
            c.content = f'<html><head><link rel="stylesheet" href="style/default.css"/></head><body><p>{html_content}</p></body></html>'
            book.add_item(c)
            spine.append(c)
            toc.append(epub.Link(f"ch{ch_num:04d}.xhtml", ch_title, f"ch{ch_num}"))
        
        book.toc = toc
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        book.spine = spine
        
        import io
        buf = io.BytesIO()
        epub.write_epub(buf, book)
        return buf.getvalue(), None


    def export_novel(self, novel_id: str, fmt: str = "txt") -> tuple:
        """导出小说全文
        
        Returns:
            (content: str|bytes|None, error: str|None)
        """
        plan = self.get_novel(novel_id)
        if not plan:
            return None, f"小说 '{novel_id}' 不存在"

        chapters_dir = os.path.join(self.memory.get_novel_dir(novel_id), "chapters")
        if not os.path.exists(chapters_dir):
            return None, "尚未生成任何章节，请先在写作页面生成至少一章"

        title = plan.get("title", novel_id)
        chapters = sorted(
            [f for f in os.listdir(chapters_dir) if f.endswith(".md")],
            key=lambda x: int(x.split("_")[1].split(".")[0]) if "_" in x else 0
        )

        if not chapters:
            return None, "暂无章节内容，请先生成章节"

        if fmt == "epub":
            return self._export_epub(title, plan, chapters_dir, chapters)

        if fmt == "txt":
            lines = [f"{title}\n{'=' * 40}\n"]
            for ch_file in chapters:
                try:
                    with open(os.path.join(chapters_dir, ch_file), "r", encoding="utf-8") as f:
                        lines.append(f.read())
                        lines.append("\n\n" + "—" * 40 + "\n\n")
                except (IOError, UnicodeDecodeError) as e:
                    log.warning(f"Failed to read {ch_file} for export: {e}")
                    lines.append(f"[无法读取: {ch_file}]\n\n")
            return "\n".join(lines), None

        return None, f"暂不支持 {fmt} 格式"

