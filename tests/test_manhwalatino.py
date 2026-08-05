from __future__ import annotations

import unittest
from pathlib import Path

from tools.generate import _madara_bundle, _supported_madara


class Response:
    def __init__(self, url, text="", content=b"", headers=None):
        self.url, self.text, self.content = url, text, content
        self.headers, self.status_code = headers or {}, 200

    def raise_for_status(self):
        pass


class Fetcher:
    def __init__(self, responses):
        self.responses, self.requests = responses, []

    async def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return self.responses.pop(0)


def source_class():
    root = Path(__file__).parents[1]
    module = root.parent / "extensions-source-main" / "src" / "es" / "manhwalatino"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_madara(module, build)
    assert config is not None
    bundle = _madara_bundle((root / "engines" / "madara.py").read_text(encoding="utf-8"), config)
    namespace = {"__name__": "test_manhwalatino_bundle"}
    exec(compile(bundle, "manhwalatino_es.py", "exec"), namespace)
    return namespace["SOURCE"]


class ManhwaLatinoTest(unittest.IsolatedAsyncioTestCase):
    async def test_details_paginated_chapters_pages_and_image_mime(self):
        details = '''<div class="post-title"><h1>Gato</h1></div><div class="summary_content">
          <div class="post-content_item"><div class="summary-heading">Estado del comic</div>
          <div class="summary-content">En curso</div></div>
          <div class="post-content_item"><div class="summary-heading">Resumen</div>
          <div class="summary-container">Una historia.</div></div></div>'''
        chapter = lambda number, date: f'''<li class="wp-manga-chapter"><div class="mini-letters">
          <a href="/manga/gato/capitulo-{number}/">Leer\nCapítulo {number}</a></div>
          <span class="chapter-release-date">{date}</span></li>'''
        first = chapter(2, "05/08/2026") + '<div class="pagination"><span class="current">1</span><span>2</span></div>'
        second = chapter(1, "04/08/2026") + '<div class="pagination"><span class="current">2</span></div>'
        reader = '''<div class="page-break"><img class="advert" src="/ad.jpg">
          <img class="wp-manga-chapter-img" data-src="/pages/2.webp"></div>'''
        fetcher = Fetcher([
            Response("https://manhwa-latino.com/manga/gato/", details),
            Response("https://manhwa-latino.com/manga/gato/", first),
            Response("https://manhwa-latino.com/manga/gato/?t=2", second),
            Response("https://manhwa-latino.com/manga/gato/capitulo-2/?style=list", reader),
            Response("https://manhwa-latino.com/pages/2.webp", content=b"image", headers={"Content-Type": "application/octet-stream"}),
        ])
        source = source_class()(fetcher)

        full = await source.details("/manga/gato/")
        chapters = await source.chapters("/manga/gato/")
        page = (await source.pages(chapters[0]))[0]
        content = await source.page_bytes(page)

        self.assertEqual((full.title, full.description, full.status), ("Gato", "Una historia.", "ongoing"))
        self.assertEqual([item.number for item in chapters], [2.0, 1.0])
        self.assertEqual(chapters[0].uploaded_at, "2026-08-05T00:00:00")
        self.assertEqual(fetcher.requests[2][2]["params"], {"t": "2"})
        self.assertEqual(page.source_id, "https://manhwa-latino.com/pages/2.webp")
        self.assertEqual((content.media_type, b"".join(content.chunks)), ("image/jpeg", b"image"))
        self.assertEqual(fetcher.requests[-1][2]["headers"]["Accept-Encoding"], "")
        self.assertEqual((source.requests_per_minute, source.capabilities.content_warning), (30, "mixed"))


if __name__ == "__main__":
    unittest.main()
