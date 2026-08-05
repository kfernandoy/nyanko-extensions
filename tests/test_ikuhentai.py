from __future__ import annotations

import unittest
from pathlib import Path

from tools.generate import _extract_kotlin_metadata, _generic_bundle, _supported_generic


class Response:
    def __init__(self, url, text, *, content=b"", headers=None):
        self.url, self.text, self.content = url, text, content
        self.status_code, self.headers = 200, headers or {}

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
    module = root.parent / "extensions-source-main" / "src" / "es" / "ikuhentai"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_generic(module, build)
    assert config is not None
    config["content_warning"] = _extract_kotlin_metadata(module)
    bundle = _generic_bundle(
        (root / "engines" / "madara.py").read_text(encoding="utf-8"),
        (root / "engines" / "generic.py").read_text(encoding="utf-8"),
        config,
    )
    namespace = {"__name__": "test_ikuhentai_bundle"}
    exec(compile(bundle, "ikuhentai_es.py", "exec"), namespace)
    return namespace["SOURCE"]


class IkuhentaiTest(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_search_and_filters_match_kotlin(self):
        listing = """
        <div class="page-listing-item"><div class="page-item-detail">
          <div class="item-thumb"><a href="/manga/gato/" title="Gato"><img data-lazy-src="/gato.jpg"></a></div>
        </div></div><a class="nextpostslink" href="/page/3/">Siguiente</a>
        """
        fetcher = Fetcher([
            Response("https://ikuhentai.net/page/2/", listing),
            Response("https://ikuhentai.net/page/3/", listing),
        ])
        source = source_class()(fetcher)

        popular = await source.browse("popular", 2)
        search = await source.search("gato", 3, {
            "author": "Ana", "release": "2026", "sort": "views",
            "statuses": ["on-going"], "genres": ["romance"],
        })

        self.assertEqual(popular["items"][0].source_id, "/manga/gato/")
        self.assertTrue(search["has_more"])
        self.assertEqual(fetcher.requests[0][2]["params"]["m_orderby"], "views")
        params = fetcher.requests[1][2]["params"]
        self.assertIn(("genre[]", "romance"), params)
        self.assertIn(("status[]", "on-going"), params)
        self.assertIn(("author", "Ana"), params)
        self.assertEqual([item.id for item in source.get_filters()], [
            "author", "release", "sort", "statuses", "genres",
        ])
        self.assertEqual(source.get_filters()[2].options[1], ("latest", "Latest"))
        self.assertEqual(source.capabilities.content_warning, "nsfw")

    async def test_details_chapters_pages_and_headers_match_kotlin(self):
        details = """
        <div class="site-content"><h1>Gato</h1><div class="summary_image"><img src="/cover.jpg"></div>
          <div class="author-content">Ana</div><div class="artist-content">Beto</div>
          <div class="genres-content"><a>Romance</a></div>
          <div class="post-content_item"><h5>Estado</h5><div class="summary-content">En emisión</div></div>
        </div><div class="description-summary">Sinopsis</div>
        """
        chapters = """
        <ul><li class="wp-manga-chapter"><a href="/manga/gato/capitulo-2/?style=paged">Capítulo 2</a>
          <span class="chapter-release-date"><i>agosto 04, 2026</i></span></li></ul>
        """
        pages = '<div class="reading-content"><div><img data-lazy-src="/pages/1.jpg"></div></div>'
        image = Response("https://ikuhentai.net/pages/1.jpg", "", content=b"jpg", headers={"Content-Type": "image/jpeg"})
        fetcher = Fetcher([
            Response("https://ikuhentai.net/manga/gato/", details),
            Response("https://ikuhentai.net/manga/gato/ajax/chapters/", chapters),
            Response("https://ikuhentai.net/manga/gato/capitulo-2/?style=list", pages),
            image,
        ])
        source = source_class()(fetcher)

        manga = await source.details("/manga/gato/")
        chapter = (await source.chapters(manga))[0]
        page = (await source.pages(chapter))[0]
        content = await source.page_bytes(page)

        self.assertEqual((manga.status, manga.content_tags), ("ongoing", ("Romance",)))
        self.assertEqual(chapter.source_id, "https://ikuhentai.net/manga/gato/capitulo-2/?style=list")
        self.assertEqual(chapter.uploaded_at, "2026-08-04T00:00:00")
        self.assertEqual(page.source_id, "https://ikuhentai.net/pages/1.jpg")
        self.assertEqual(b"".join(content.chunks), b"jpg")
        self.assertEqual(fetcher.requests[-1][2]["headers"]["Referer"], "https://ikuhentai.net/")


if __name__ == "__main__":
    unittest.main()
