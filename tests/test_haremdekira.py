from __future__ import annotations

import unittest
from pathlib import Path

from tools.generate import _madara_bundle, _supported_madara


class Response:
    def __init__(self, url, text):
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
    module = root.parent / "extensions-source-main" / "src" / "es" / "haremdekira"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_madara(module, build)
    assert config is not None
    bundle = _madara_bundle((root / "engines" / "madara.py").read_text(encoding="utf-8"), config)
    namespace = {"__name__": "test_haremdekira_bundle"}
    exec(compile(bundle, "haremdekira_es.py", "exec"), namespace)
    return namespace["SOURCE"]


class HaremDeKiraTest(unittest.IsolatedAsyncioTestCase):
    async def test_always_ajax_uses_custom_popular_and_search_cards(self):
        popular_html = """
        <div class="latest-poster">
          <a class="bg-cover" style="background-image: url('/gato.jpg')" href="/serie/gato"></a>
          <h3>Gato</h3>
        </div>
        """
        search_html = """
        <button class="group"><div class="grid"><a href="/serie/perro">
          <div class="bg-cover" style="background-image: url('/perro.jpg')"></div><h3>Perro</h3>
        </a></div></button>
        """
        fetcher = Fetcher([
            Response("https://kiraproject.lat/wp-admin/admin-ajax.php", popular_html),
            Response("https://kiraproject.lat/wp-admin/admin-ajax.php", search_html),
        ])
        source = source_class()(fetcher)

        popular = await source.browse("popular", 2)
        search = await source.search(" perro ", 3, {"status": ["on-going"], "order": "latest"})

        popular_data = dict(fetcher.requests[0][2]["data"])
        search_data = dict(fetcher.requests[1][2]["data"])
        self.assertEqual((popular_data["page"], popular_data["vars[meta_key]"]), ("1", "_wp_manga_views"))
        self.assertEqual((search_data["page"], search_data["template"], search_data["vars[s]"]), (
            "2", "madara-core/content/content-search", "perro",
        ))
        self.assertEqual(fetcher.requests[0][2]["headers"], {"X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(popular["items"][0].cover_url, "https://kiraproject.lat/gato.jpg")
        self.assertEqual(search["items"][0].title, "Perro")
        self.assertEqual(source.capabilities.requests_per_minute, 180)
        self.assertEqual(source.capabilities.content_warning, "mixed")

    async def test_custom_details_and_anchor_chapters_match_kotlin(self):
        details = """
        <div class="wp-manga">
          <div class="grid"><h1>Gato</h1></div>
          <div alt="type"><span>En Curso</span></div>
          <div alt="type"><span>Acción</span></div>
          <div alt="type"><span>Romance</span></div>
          <div id="expand_content"><p>Uno</p><p>Dos</p></div>
        </div>
        <div class="summary_image"><img src="/cover.jpg"></div>
        <div class="author-content"><a>Ana</a></div>
        <div class="artist-content"><a>Leo</a></div>
        """
        chapters_html = """
        <ul id="list-chapters"><li><a href="/leer/gato-3">
          <div class="grid"><span>Capitulo 3</span><div>agosto 04, 2026</div></div>
        </a></li></ul>
        """
        fetcher = Fetcher([
            Response("https://kiraproject.lat/serie/gato/", details),
            Response("https://kiraproject.lat/serie/gato/", chapters_html),
        ])
        source = source_class()(fetcher)

        search = await source.search("https://kiraproject.lat/serie/gato/")
        chapters = await source.chapters("https://kiraproject.lat/serie/gato/")

        manga = search["items"][0]
        self.assertEqual((manga.title, manga.status, manga.content_tags), (
            "Gato", "ongoing", ("Acción", "Romance"),
        ))
        self.assertEqual((manga.author, manga.artist, manga.description), ("Ana", "Leo", "Uno\n\nDos"))
        self.assertEqual(chapters[0].source_id, "https://kiraproject.lat/leer/gato-3")
        self.assertEqual(chapters[0].title, "Capitulo 3")
        self.assertEqual(chapters[0].uploaded_at, "2026-08-04T00:00:00")
        self.assertNotIn("?style=list", chapters[0].source_id)


if __name__ == "__main__":
    unittest.main()
