from __future__ import annotations

import base64
import json
import unittest
from pathlib import Path

from tools.generate import _extract_kotlin_metadata, _generic_bundle, _supported_generic


class Response:
    def __init__(self, url, text=""):
        self.url, self.text, self.status_code = url, text, 200

    def raise_for_status(self):
        pass

    def json(self):
        return json.loads(self.text)


class Fetcher:
    def __init__(self, responses):
        self.responses, self.requests = responses, []

    async def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return self.responses.pop(0)


def source_class():
    root = Path(__file__).parents[1]
    module = root.parent / "extensions-source-main" / "src" / "es" / "insanosscan"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_generic(module, build)
    assert config is not None
    config["content_warning"] = _extract_kotlin_metadata(module)
    bundle = _generic_bundle(
        (root / "engines" / "madara.py").read_text(encoding="utf-8"),
        (root / "engines" / "generic.py").read_text(encoding="utf-8"),
        config,
    )
    namespace = {"__name__": "test_insanosscan_bundle"}
    exec(compile(bundle, "insanosscan_es.py", "exec"), namespace)
    return namespace["SOURCE"]


class InsanosScanTest(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_and_nonce_search_match_kotlin(self):
        listing = """
        <article class="catalog-card"><a class="catalog-card__link" href="/manga/gato/"></a>
          <h2 class="catalog-card__title">Gato</h2><img class="catalog-card__cover" src="/gato.jpg"></article>
        <div class="catalog-pagination"><a class="page-numbers next">Siguiente</a></div>
        """
        encoded = base64.b64encode(b'window.adar = {"nonce":"abc123"};').decode()
        search = json.dumps({"data": [{"url": "/manga/perro/", "title": "Perro", "cover": "/perro.jpg"}]})
        fetcher = Fetcher([
            Response("https://insanoslibrary.com/manga/", listing),
            Response("https://insanoslibrary.com", f'<script id="adar-main-js-extra" src="data:text/javascript;base64,{encoded}"></script>'),
            Response("https://insanoslibrary.com/wp-admin/admin-ajax.php", search),
        ])
        source = source_class()(fetcher)

        popular = await source.browse("popular", 2)
        found = await source.search("perro")

        self.assertEqual((popular["items"][0].title, popular["has_more"]), ("Gato", True))
        self.assertEqual(fetcher.requests[0][2]["params"], {"orderby": "views", "page": "2"})
        self.assertEqual(fetcher.requests[-1][2]["data"], {
            "action": "adar_search", "nonce": "abc123", "query": "perro",
        })
        self.assertEqual(found["items"][0].source_id, "/manga/perro/")
        self.assertFalse(source.get_preferences()[0].default)
        self.assertEqual(source.capabilities.content_warning, "safe")

    async def test_details_paid_chapters_and_reader_match_kotlin(self):
        details = """
        <h1 class="series-main-title">Gato</h1><img class="series-cover-img" src="/cover.jpg">
        <div class="synopsis-content">Sinopsis</div><span class="data-badge--status">Finalizado</span>
        <table><td class="genres-cell"><a class="genre-pill">Acción</a></td></table>
        """
        chapters = """
        <script>var locked = {"/manga/gato/capitulo-2/": 4};</script><div class="chapters-list">
          <a class="chapter-row" href="/manga/gato/capitulo-2/"><span class="chapter-row__num">Capítulo 2</span><span class="chapter-row__date">04 ago 2026</span></a>
          <a class="chapter-row" href="/manga/gato/capitulo-1/"><span class="chapter-row__title">Capítulo 1</span><span class="chapter-row__date">03 ago 2026</span></a>
        </div>
        """
        pages = """
        <div><div class="reader-pages"></div><div><img src="/pages/1.jpg"></div><div><img data-src="/pages/2.jpg"></div></div>
        """
        fetcher = Fetcher([
            Response("https://insanoslibrary.com/manga/gato/", details),
            Response("https://insanoslibrary.com/manga/gato/", chapters),
            Response("https://insanoslibrary.com/manga/gato/", chapters),
            Response("https://insanoslibrary.com/manga/gato/capitulo-2/", pages),
        ])
        source = source_class()(fetcher)

        manga = await source.details("/manga/gato/")
        free = await source.chapters(manga)
        source.preferences = {"show_paid_chapters": True}
        all_chapters = await source.chapters(manga)
        page_list = await source.pages(all_chapters[0])

        self.assertEqual((manga.status, manga.content_tags), ("completed", ("Acción",)))
        self.assertEqual([chapter.number for chapter in free], [1.0])
        self.assertEqual(all_chapters[0].title, "Capítulo 2 🔒")
        self.assertEqual(all_chapters[0].uploaded_at, "2026-08-04T00:00:00")
        self.assertEqual([page.source_id for page in page_list], [
            "https://insanoslibrary.com/pages/1.jpg", "https://insanoslibrary.com/pages/2.jpg",
        ])


if __name__ == "__main__":
    unittest.main()
