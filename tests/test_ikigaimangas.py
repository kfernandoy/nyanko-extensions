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
    module = root.parent / "extensions-source-main" / "src" / "es" / "ikigaimangas"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_generic(module, build)
    assert config is not None
    config["content_warning"] = _extract_kotlin_metadata(module)
    bundle = _generic_bundle(
        (root / "engines" / "madara.py").read_text(encoding="utf-8"),
        (root / "engines" / "generic.py").read_text(encoding="utf-8"),
        config,
    )
    namespace = {"__name__": "test_ikigaimangas_bundle"}
    exec(compile(bundle, "ikigaimangas_es.py", "exec"), namespace)
    return namespace["SOURCE"]


class IkigaiMangasTest(unittest.IsolatedAsyncioTestCase):
    async def test_popular_search_filters_and_preferences_match_kotlin(self):
        popular_html = """
        <div class="grid"><div class="card"><img src="/gato.jpg">
          <div class="card-body"><h3 class="card-title">Gato</h3></div>
          <div class="card-actions"><a class="btn" href="/series/gato/">Abrir</a></div>
        </div></div>
        """
        search_html = """
        <section aria-labelledby="archive-heading"><ul class="grid">
          <a class="card" href="/series/perro/"><img src="/perro.jpg"><h3>Perro grande</h3></a>
        </ul></section>
        <nav aria-label="pagination"><a class="btn">1</a><a class="btn">Siguiente</a></nav>
        """
        fetcher = Fetcher([
            Response("https://zonaikigai.gamesview.shop/clasificacion/", popular_html),
            Response("https://zonaikigai.gamesview.shop/series/", search_html),
        ])
        source = source_class()(fetcher)
        source.preferences = {"show_nsfw": True}

        popular = await source.browse("popular", 9)
        search = await source.search("perro", 2, {
            "genres": ["906397894527549443"],
            "statuses": ["911437469204086787"],
            "sort": "name", "direction": "asc",
        })

        self.assertEqual(popular["items"][0].source_id, "gato")
        self.assertEqual(search["items"][0].title, "Perro grande")
        self.assertTrue(search["has_more"])
        params = fetcher.requests[1][2]["params"]
        self.assertIn(("generos[]", "906397894527549443"), params)
        self.assertIn(("estados[]", "911437469204086787"), params)
        self.assertIn(("ordenar", "name"), params)
        self.assertEqual(fetcher.requests[1][2]["headers"]["Cookie"], "is-adult-enabled=true")
        self.assertEqual([item.id for item in source.get_filters()], [
            "sort", "direction", "statuses", "genres",
        ])
        self.assertEqual(source.get_filters()[0].options[0], ("name", "Nombre"))
        self.assertFalse(source.get_preferences()[0].default)
        self.assertEqual(source.capabilities.content_warning, "mixed")

    async def test_details_chapters_and_pages_match_kotlin(self):
        details_html = """
        <article class="card"><img src="/cover.jpg"><div class="card-body">
          <h1 class="card-title">Gato</h1><p>Sinopsis</p>
          <ul><li><a href="?estados=1">En Curso</a></li><li><a href="?generos=2">Romance</a></li></ul>
        </div></article>
        """
        chapters_html = """
        <section class="card"><ul class="grid"><a class="card" href="/series/gato/capitulo-2/">
          <div class="card-body"><h3 class="card-title">Capitulo 2</h3></div>
          <time datetime="Mon Aug 04 2026 10:11:12 GMT-0400 (Chile)"></time>
        </a></ul></section>
        """
        pages_html = '<section><div class="img"><img src="/pages/1.jpg"></div></section>'
        fetcher = Fetcher([
            Response("https://zonaikigai.gamesview.shop/series/gato/", details_html),
            Response("https://zonaikigai.gamesview.shop/series/gato/?pagina=1", chapters_html),
            Response("https://zonaikigai.gamesview.shop/series/gato/capitulo-2/", pages_html),
        ])
        source = source_class()(fetcher)

        details = await source.details("gato")
        chapters = await source.chapters("gato")
        pages = await source.pages(chapters[0])

        self.assertEqual((details.title, details.status, details.content_tags), (
            "Gato", "ongoing", ("Romance",),
        ))
        self.assertEqual(details.description, "Sinopsis")
        self.assertEqual(chapters[0].source_id, "/series/gato/capitulo-2/")
        self.assertEqual(chapters[0].number, 2.0)
        self.assertEqual(chapters[0].uploaded_at, "2026-08-04T10:11:12-04:00")
        self.assertEqual(pages[0].source_id, "https://zonaikigai.gamesview.shop/pages/1.jpg")


if __name__ == "__main__":
    unittest.main()
