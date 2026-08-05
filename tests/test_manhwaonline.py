from __future__ import annotations

import unittest
from pathlib import Path

from tools.generate import _madara_bundle, _supported_madara


class Response:
    def __init__(self, url, text=""):
        self.url, self.text, self.status_code = url, text, 200

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
    module = root.parent / "extensions-source-main" / "src" / "es" / "manhwaonline"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_madara(module, build)
    assert config is not None
    bundle = _madara_bundle((root / "engines" / "madara.py").read_text(encoding="utf-8"), config)
    namespace = {"__name__": "test_manhwaonline_bundle"}
    exec(compile(bundle, "manhwaonline_es.py", "exec"), namespace)
    return namespace["SOURCE"]


class ManhwaOnlineTest(unittest.IsolatedAsyncioTestCase):
    async def test_standard_madara_flow_and_metadata(self):
        listing = '''<div class="page-item-detail manga"><div class="item-thumb">
          <a href="/manga/gato/"><img src="/gato.jpg"></a></div>
          <div class="post-title"><a href="/manga/gato/">Gato</a></div></div>'''
        details = '''<div class="post-title"><h1>Gato</h1></div>
          <div class="description-summary"><div class="summary__content"><p>Una historia.</p></div></div>
          <div class="summary_content"><div class="post-content_item">
          <div class="summary-heading">Estado</div><div class="summary-content">Finalizado</div></div></div>'''
        holder = '<div id="manga-chapters-holder-1" data-id="7"></div>'
        chapters = '''<li class="wp-manga-chapter"><a href="/manga/gato/capitulo-3/">Capítulo 3</a>
          <span class="chapter-release-date">agosto 05, 2026</span></li>'''
        reader = '<div class="reading-content"><div class="page-break"><img data-src="/pages/3.webp"></div></div>'
        fetcher = Fetcher([
            Response("https://manhwa-online.com/manga/", listing),
            Response("https://manhwa-online.com/manga/gato/", details),
            Response("https://manhwa-online.com/manga/gato/", holder),
            Response("https://manhwa-online.com/manga/gato/ajax/chapters", chapters),
            Response("https://manhwa-online.com/manga/gato/capitulo-3/?style=list", reader),
        ])
        source = source_class()(fetcher)

        manga = (await source.browse("popular"))[0]
        full = await source.details(manga)
        chapter = (await source.chapters(manga))[0]
        page = (await source.pages(chapter))[0]

        self.assertEqual((manga.title, manga.cover_url), ("Gato", "https://manhwa-online.com/gato.jpg"))
        self.assertEqual((full.description, full.status), ("Una historia.", "completed"))
        self.assertEqual((chapter.number, chapter.uploaded_at), (3.0, "2026-08-05T00:00:00"))
        self.assertEqual(page.source_id, "https://manhwa-online.com/pages/3.webp")
        self.assertEqual(fetcher.requests[3][0:2], ("POST", "https://manhwa-online.com/manga/gato/ajax/chapters"))
        self.assertEqual((source.load_more, source.use_new_chapter_endpoint), ("never", True))
        self.assertEqual((source.date_locale, source.capabilities.content_warning), ("es", "nsfw"))


if __name__ == "__main__":
    unittest.main()
