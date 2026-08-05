from __future__ import annotations

import unittest
from pathlib import Path

from tools.generate import _madara_bundle, _supported_madara


class Response:
    def __init__(self, url, text="", payload=None):
        self.url, self.text, self.payload, self.status_code = url, text, payload, 200

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class Fetcher:
    def __init__(self, responses):
        self.responses, self.requests = responses, []

    async def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return self.responses.pop(0)


def source_class():
    root = Path(__file__).parents[1]
    module = root.parent / "extensions-source-main" / "src" / "es" / "mangasnosekai"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_madara(module, build)
    assert config is not None
    bundle = _madara_bundle((root / "engines" / "madara.py").read_text(encoding="utf-8"), config)
    namespace = {"__name__": "test_mangasnosekai_bundle"}
    exec(compile(bundle, "mangasnosekai_es.py", "exec"), namespace)
    return namespace["SOURCE"]


class MangasNoSekaiTest(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_details_paginated_chapters_and_pages(self):
        listing = '''<div class="page-listing-item"><div class="row"><div>
          <a href="/manga/gato/"><img data-src="/gato.jpg"><figcaption>Gato</figcaption></a>
        </div></div></div>'''
        details = '''<div class="thumble-container"><p class="titleMangaSingle">Gato</p>
          <img class="img-responsive" src="/cover.jpg"></div>
          <section id="section-sinopsis"><p>Sinopsis.</p>
          <div class="d-flex"><div>Estado</div><p>En curso</p></div>
          <div class="d-flex"><div>Autor</div><p><a>Ana</a></p></div>
          <div class="d-flex"><div>Generos</div><p><a>acción</a></p></div>
          <div class="d-flex"><div>Otros nombres</div><p>Cat</p></div></section>
          <script id="wp-manga-js" src="/js/core.js"></script>
          <script id="wp-manga-js-extra">var manga={"manga_id":"77"};</script>'''
        core = '''var a=['/api/chapters','unused','token'];
          var d=function(i){i=i-0x10;var x=a[i];return x;};
          function load(){jQuery.ajax({url:d('0x10'),data:{token:d('0x12')}});}'''
        chapter = lambda name, link, date: {"name": name, "link": link, "date": date}
        first = {"chapters_to_display": [chapter("Capítulo 2", "/manga/gato/capitulo-2/", "agosto 05, 2026")], "current_page": 1, "total_pages": 2}
        second = {"chapters_to_display": [chapter("Capítulo 1", "/manga/gato/capitulo-1/", "agosto 04, 2026")], "current_page": 2, "total_pages": 2}
        reader = '<div class="reading-content"><div class="page-break"><img data-src="/pages/2.jpg"></div></div>'
        fetcher = Fetcher([
            Response("https://mangasnosekai.com/biblioteca/", listing),
            Response("https://mangasnosekai.com/manga/gato/", details),
            Response("https://mangasnosekai.com/manga/gato/", details),
            Response("https://mangasnosekai.com/js/core.js", core),
            Response("https://mangasnosekai.com/api/chapters", payload=first),
            Response("https://mangasnosekai.com/api/chapters", payload=second),
            Response("https://mangasnosekai.com/manga/gato/capitulo-2/", reader),
        ])
        source = source_class()(fetcher)

        manga = (await source.browse("popular"))[0]
        full = await source.details(manga)
        chapters = await source.chapters(manga)
        pages = await source.pages(chapters[0])

        self.assertEqual((manga.title, manga.cover_url), ("Gato", "https://mangasnosekai.com/gato.jpg"))
        self.assertEqual((full.author, full.status, full.content_tags), ("Ana", "ongoing", ("Acción",)))
        self.assertEqual(full.description, "Sinopsis.\n\nOtros nombres: Cat")
        self.assertEqual([item.number for item in chapters], [2.0, 1.0])
        self.assertEqual(chapters[0].uploaded_at, "2026-08-05T00:00:00")
        self.assertEqual(pages[0].source_id, "https://mangasnosekai.com/pages/2.jpg")
        self.assertEqual(fetcher.requests[4][2]["data"], {"mangaid": "77", "page": "1", "token": "token"})
        self.assertEqual((source.requests_per_minute, source.capabilities.content_warning), (120, "safe"))


if __name__ == "__main__":
    unittest.main()
