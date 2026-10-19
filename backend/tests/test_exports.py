import io
from pathlib import Path
from types import SimpleNamespace
import zipfile
import xml.etree.ElementTree as ET

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.mixins.export import ExportMixin


class ExportEngine(ExportMixin):
    def __init__(self, folder):
        self.memory = SimpleNamespace(get_novel_dir=lambda novel_id: str(folder))
    def get_novel(self, novel_id):
        return {"title":"长街听雨", "genre":"古风", "style":"温馨", "characters":{"protagonist":{"name":"苏晚"}}}


@pytest.fixture
def exports(tmp_path, monkeypatch):
    from api.routers import novels
    (tmp_path/'chapters').mkdir()
    (tmp_path/'chapters/chapter_0001.md').write_text('# 雨夜来客\n\n苏晚推开窗，雨还在下。\n\n<script>这只是小说里的文字</script>',encoding='utf-8')
    monkeypatch.setattr(novels, 'engine', ExportEngine(tmp_path))
    monkeypatch.setattr(novels, '_require_legacy_owner', lambda request, novel_id: 'test-owner')
    app=FastAPI();app.include_router(novels.router)
    return TestClient(app)


def test_txt_export_preserves_chinese(exports):
    response=exports.get('/api/novels/sample/export?fmt=txt')
    assert response.status_code==200
    assert '苏晚推开窗' in response.text
    assert response.headers['content-type'].startswith('text/plain')


def test_epub_is_a_real_archive_and_text_is_escaped(exports):
    response=exports.get('/api/novels/sample/export?fmt=epub')
    assert response.status_code==200
    with zipfile.ZipFile(io.BytesIO(response.content)) as book:
        assert book.read('mimetype')==b'application/epub+zip'
        assert book.infolist()[0].filename == 'mimetype'
        assert book.infolist()[0].compress_type == zipfile.ZIP_STORED
        for name in book.namelist():
            if name.endswith(('.xml', '.opf', '.xhtml')):
                ET.fromstring(book.read(name))
        package = ET.fromstring(book.read('OEBPS/content.opf'))
        ns = {'opf': 'http://www.idpf.org/2007/opf'}
        manifest = package.find('opf:manifest', ns)
        for item in manifest:
            assert 'OEBPS/' + item.attrib['href'] in book.namelist()
        ids = {item.attrib['id'] for item in manifest}
        for item in package.find('opf:spine', ns):
            assert item.attrib['idref'] in ids
        chapters=[book.read(name).decode() for name in book.namelist() if 'ch0001' in name]
        assert chapters and '苏晚推开窗' in chapters[0]
        assert '<script>' not in chapters[0]


def test_pdf_export_remains_unavailable(exports):
    response=exports.get('/api/novels/sample/export?fmt=pdf')
    assert response.status_code==400


def test_unknown_export_format_is_rejected(exports):
    assert exports.get('/api/novels/sample/export?fmt=bad').status_code==400
