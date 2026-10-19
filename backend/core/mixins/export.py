"""NovelEngine ExportMixin — 导出 — TXT / EPUB

由 tools/split_engine.py 从 engine.py 自动拆分。
依赖 NovelEngine 提供的 self.client/self.model/self.memory 等属性。
"""
import logging

log = logging.getLogger(__name__)

import os
import re
from html import escape
from pathlib import Path
from typing import AsyncGenerator, Optional, AsyncIterator




class ExportMixin:
    def _export_pdf(self, title: str, chapters_dir: str, chapters: list) -> tuple:
        try:
            from fpdf import FPDF
            fonts = [os.getenv("NOVELGEN_CJK_FONT", ""), "C:/Windows/Fonts/simhei.ttf",
                     "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
                     "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                     "/system/fonts/NotoSansCJK-Regular.ttc"]
            font = next((path for path in fonts if path and os.path.isfile(path)), None)
            if not font:
                return None, "PDF 导出需要中文字体，请在 NOVELGEN_CJK_FONT 中指定字体文件"
            pdf = FPDF(format="A5")
            pdf.set_margins(16, 18, 16)
            pdf.set_auto_page_break(auto=True, margin=18)
            pdf.add_font("CJK", fname=font)
            pdf.set_title(title)
            pdf.set_author("墨局")
            pdf.add_page()
            pdf.set_font("CJK", size=18)
            pdf.multi_cell(0, 12, title, align="C", new_x="LMARGIN", new_y="NEXT")
            pdf.ln(7)
            for filename in chapters:
                content = Path(chapters_dir, filename).read_text(encoding="utf-8")
                for paragraph in content.splitlines():
                    heading = paragraph.startswith("#")
                    pdf.set_font("CJK", size=14 if heading else 10.5)
                    text = paragraph.lstrip("# ") if heading else paragraph
                    pdf.multi_cell(0, 7, text or " ", new_x="LMARGIN", new_y="NEXT")
                pdf.ln(5)
            return bytes(pdf.output()), None
        except (ImportError, OSError, ValueError) as exc:
            log.warning("PDF export failed (%s)", type(exc).__name__)
            return None, "PDF 导出失败，请检查中文字体与 fpdf2 是否可用"

    def _export_epub(self, title: str, plan: dict, chapters_dir: str, chapters: list) -> tuple:
        """Write EPUB 3 using stdlib so Android needs no native XML package."""
        import io
        import uuid
        import zipfile
        from datetime import datetime, timezone

        def xml(value):
            return escape(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value)))

        def page(label, body):
            return ('<?xml version="1.0" encoding="utf-8"?>'
                    '<html xmlns="http://www.w3.org/1999/xhtml" lang="zh-CN" xml:lang="zh-CN">'
                    f'<head><title>{xml(label)}</title><link rel="stylesheet" type="text/css" href="style/default.css"/></head>'
                    f'<body>{body}</body></html>')

        pages = [("intro.xhtml", "书名页", page(title, f'<h1>{xml(title)}</h1>'
                  f'<p>题材：{xml(plan.get("genre", ""))} | 风格：{xml(plan.get("style", ""))}</p>'))]
        for index, filename in enumerate(chapters, 1):
            try:
                content = Path(chapters_dir, filename).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                return None, f"无法读取章节 {filename}，请检查后重新导出"
            label = f"第{index}章"
            body = "<p>" + xml(content).replace("\n\n", "</p><p>").replace("\n", "<br/>") + "</p>"
            pages.append((f"ch{index:04d}.xhtml", label, page(label, body)))

        identifier = f"urn:uuid:{uuid.uuid4()}"
        modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        author = plan.get("characters", {}).get("protagonist", {}).get("name", "AI Writer")
        manifest = ''.join(f'<item id="p{i}" href="{name}" media-type="application/xhtml+xml"/>'
                           for i, (name, _, _) in enumerate(pages))
        spine = ''.join(f'<itemref idref="p{i}"/>' for i in range(len(pages)))
        navigation = ''.join(f'<li><a href="{name}">{xml(label)}</a></li>' for name, label, _ in pages)
        package = ('<?xml version="1.0" encoding="utf-8"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f'<dc:identifier id="book-id">{identifier}</dc:identifier><dc:title>{xml(title)}</dc:title>'
            f'<dc:language>zh-CN</dc:language><dc:creator>{xml(author)}</dc:creator>'
            f'<meta property="dcterms:modified">{modified}</meta></metadata><manifest>{manifest}'
            '<item id="nav" href="nav.xhtml" properties="nav" media-type="application/xhtml+xml"/>'
            '<item id="style" href="style/default.css" media-type="text/css"/>'
            f'</manifest><spine>{spine}</spine></package>')
        nav = ('<?xml version="1.0" encoding="utf-8"?>'
               '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="zh-CN">'
               f'<head><title>目录</title></head><body><nav epub:type="toc" id="toc"><h1>{xml(title)}</h1>'
               f'<ol>{navigation}</ol></nav></body></html>')
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as book:
            book.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
            book.writestr("META-INF/container.xml", '<?xml version="1.0" encoding="utf-8"?>'
                '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
                '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
            book.writestr("OEBPS/content.opf", package)
            book.writestr("OEBPS/nav.xhtml", nav)
            book.writestr("OEBPS/style/default.css", "body{font-family:serif;line-height:1.8;margin:2em}p{text-indent:2em;margin:.5em 0}h1{text-align:center}")
            for name, _, content in pages:
                book.writestr(f"OEBPS/{name}", content)
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
            key=lambda x: (int(re.search(r"\d+", x)[0]) if re.search(r"\d+", x) else 0, x)
        )

        if not chapters:
            return None, "暂无章节内容，请先生成章节"

        if fmt == "epub":
            return self._export_epub(title, plan, chapters_dir, chapters)
        if fmt == "pdf":
            return self._export_pdf(title, chapters_dir, chapters)

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

