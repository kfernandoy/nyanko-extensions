from __future__ import annotations

import unittest
from pathlib import Path

from tools.generate import _madara_bundle, _supported_madara


class Response:
    def __init__(self, url, text, status=200):
        self.url, self.text, self.status_code = url, text, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise ValueError(self.status_code)


class Fetcher:
    def __init__(self, responses):
        self.responses, self.requests = responses, []

    async def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return self.responses.pop(0)


def source_class():
    root = Path(__file__).parents[1]
    module = root.parent / "extensions-source-main" / "src" / "es" / "esmi2manga"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_madara(module, build)
    assert config is not None
    bundle = _madara_bundle((root / "engines" / "madara.py").read_text(encoding="utf-8"), config)
    namespace = {"__name__": "test_esmi2manga_bundle"}
    exec(compile(bundle, "esmi2manga_es.py", "exec"), namespace)
    return namespace["SOURCE"]


POPULAR = '''<div class="site-content">
    <div class="page-item-detail manga"><div class="post-title"><a href="/manga/uno/"><span>Extra</span>Uno</a></div><img src="/uno.jpg"></div>
    <div class="page-item-detail manga"><a href="https://bilibilicomics.com/x">Bili</a></div>
    <nav class="navigation-ajax"></nav></div>
    <div class="page-item-detail manga"><div class="post-title"><a href="/manga/fuera/">Fuera</a></div></div>'''


class EsMi2MangaTest(unittest.IsolatedAsyncioTestCase):
    async def test_exact_popular_selector_detects_and_uses_load_more(self):
        ajax = '''<div class="site-content"><div class="page-item-detail manga">
            <div class="post-title"><a href="/manga/dos/">Dos</a></div></div></div>'''
        fetcher = Fetcher([
            Response("https://es.mi2manga.com/manga/?m_orderby=views", POPULAR),
            Response("https://es.mi2manga.com/wp-admin/admin-ajax.php", ajax),
        ])
        source = source_class()(fetcher)

        first = await source.browse("popular")
        second = await source.browse("popular", 2)

        self.assertEqual([item.title for item in first["items"]], ["Uno"])
        self.assertTrue(first["has_more"])
        self.assertEqual(fetcher.requests[1][0:2], ("POST", "https://es.mi2manga.com/wp-admin/admin-ajax.php"))
        data = fetcher.requests[1][2]["data"]
        self.assertIn(("page", "1"), data)
        self.assertIn(("vars[meta_query][0][value]", "manga"), data)
        self.assertEqual([item.title for item in second["items"]], ["Dos"])

    async def test_search_filters_exact_selector_and_spanish_chapter_date(self):
        search_html = '''<div class="site-content"><div class="c-tabs-item__content">
            <div class="post-title"><a href="/manga/gato/">Gato</a></div><img data-src="/gato.jpg"></div></div>
            <div class="c-tabs-item__content"><div class="post-title"><a href="/manga/fuera/">Fuera</a></div></div>'''
        chapters_html = '''<ul><li class="wp-manga-chapter"><a href="/manga/gato/capitulo-2/?style=paged">Capitulo 2</a>
            <span class="chapter-release-date">agosto 04, 2026</span></li></ul>'''
        fetcher = Fetcher([
            Response("https://es.mi2manga.com/page/3/?s=gato", search_html),
            Response("https://es.mi2manga.com/manga/gato/", chapters_html),
        ])
        source = source_class()(fetcher)

        search = await source.search("gato", 3, {
            "author": "Ana", "status": ["on-going"], "order": "rating",
            "adult": "0", "genre_condition": "1", "genres": ["accion"],
        })
        chapters = await source.chapters("https://es.mi2manga.com/manga/gato/")

        params = fetcher.requests[0][2]["params"]
        self.assertIn(("author", "Ana"), params)
        self.assertIn(("status[]", "on-going"), params)
        self.assertIn(("m_orderby", "rating"), params)
        self.assertIn(("adult", "0"), params)
        self.assertIn(("op", "1"), params)
        self.assertIn(("genre[]", "accion"), params)
        self.assertEqual([item.title for item in search["items"]], ["Gato"])
        self.assertEqual(chapters[0].source_id, "https://es.mi2manga.com/manga/gato/capitulo-2/?style=list")
        self.assertEqual(chapters[0].uploaded_at, "2026-08-04T00:00:00")
        self.assertEqual(source.capabilities.requests_per_minute, 120)
        self.assertEqual(source.capabilities.content_warning, "nsfw")

    async def test_direct_url_search_uses_default_madara_details(self):
        details = '''<div class="post-title"><h1>Gato</h1></div>
            <div class="summary_image"><img src="/poster.jpg"></div>
            <div class="description-summary"><div class="summary__content">Sinopsis</div></div>
            <div class="author-content"><a>Ana</a></div><div class="artist-content"><a>Leo</a></div>
            <div class="genres-content"><a>Accion</a></div><div class="summary-content">En curso</div>'''
        fetcher = Fetcher([Response("https://es.mi2manga.com/manga/gato/", details)])
        source = source_class()(fetcher)

        result = await source.search("https://es.mi2manga.com/manga/gato/")

        item = result["items"][0]
        self.assertEqual((item.title, item.author, item.artist), ("Gato", "Ana", "Leo"))
        self.assertEqual(item.description, "Sinopsis")
        self.assertEqual(item.status, "ongoing")
        self.assertEqual(item.content_tags, ("Accion",))


if __name__ == "__main__":
    unittest.main()
