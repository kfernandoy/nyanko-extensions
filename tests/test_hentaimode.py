from __future__ import annotations

import unittest
from pathlib import Path

from tools.generate import _extract_kotlin_metadata, _generic_bundle, _supported_generic


class Response:
    def __init__(self, url, text="", *, content=b"", headers=None):
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
    module = root.parent / "extensions-source-main" / "src" / "es" / "hentaimode"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_generic(module, build)
    assert config is not None
    config["content_warning"] = _extract_kotlin_metadata(module)
    bundle = _generic_bundle(
        (root / "engines" / "madara.py").read_text(encoding="utf-8"),
        (root / "engines" / "generic.py").read_text(encoding="utf-8"),
        config,
    )
    namespace = {"__name__": "test_hentaimode_bundle"}
    exec(compile(bundle, "hentaimode_es.py", "exec"), namespace)
    return namespace["SOURCE"]


LISTING = """
<div class="row"><div class="featured book-list-main"><a href="/g/10">
  <img src="/covers/10.jpg"><div class="book-description"><p>Obra diez</p></div>
</a></div></div>
<div class="book-list-main"><a href="/g/ignored"><div class="book-description"><p>Ignorada</p></div></a></div>
"""

DETAILS = """
<div id="cover"><img src="/covers/10.jpg"></div>
<div id="info-block"><div id="info"><h1>Obra diez</h1>
  <div class="tag-container">Categorías: <a class="tag">Romance</a><a class="tag">Yuri</a></div>
  <div class="tag-container">Grupo: <a class="tag">Grupo A</a></div>
  <div class="tag-container">Artista: <a class="tag">Ana</a></div>
  <div class="tag-container">Serie: <a class="tag">Original</a></div>
  <div class="tag-container">Tipo: <a class="tag">Doujinshi</a></div>
  <div class="tag-container">Personajes: <a class="tag">Uno</a><a class="tag">Dos</a></div>
  <div class="tag-container">Idioma: <a class="tag">Español</a></div>
</div></div>
"""


class HentaiModeTest(unittest.IsolatedAsyncioTestCase):
    async def test_popular_and_text_search_use_the_exact_non_paginated_listing(self):
        fetcher = Fetcher([
            Response("https://hentaimode.com", LISTING),
            Response("https://hentaimode.com/buscar?s=obra", LISTING),
        ])
        source = source_class()(fetcher)

        popular = await source.browse("popular", 8)
        latest = await source.browse("latest", 1)
        search = await source.search("obra", 4)

        self.assertEqual([request[1] for request in fetcher.requests], [
            "https://hentaimode.com", "https://hentaimode.com/buscar",
        ])
        self.assertEqual(fetcher.requests[1][2]["params"], {"s": "obra"})
        self.assertEqual([item.title for item in popular["items"]], ["Obra diez"])
        self.assertEqual(search["items"][0].cover_url, "https://hentaimode.com/covers/10.jpg")
        self.assertFalse(popular["has_more"])
        self.assertEqual(latest, {"items": [], "has_more": False})
        self.assertEqual(source.capabilities.requests_per_minute, 120)
        self.assertEqual(source.capabilities.content_warning, "nsfw")

    async def test_url_search_details_and_generated_chapter_match_kotlin(self):
        fetcher = Fetcher([
            Response("https://hentaimode.com/g/10", DETAILS),
            Response("https://hentaimode.com/g/10", DETAILS),
        ])
        source = source_class()(fetcher)

        direct = await source.search("https://hentaimode.com/g/10")
        details = await source.details("https://hentaimode.com/g/10")
        chapters = await source.chapters(details)

        self.assertEqual(direct["items"][0].source_id, "https://hentaimode.com/g/10")
        self.assertEqual((details.author, details.artist, details.status), ("Grupo A", "Ana", "completed"))
        self.assertEqual(details.content_tags, ("Romance", "Yuri"))
        self.assertEqual(details.description, "Serie: Original\nTipo: Doujinshi\nPersonajes: Uno, Dos\nIdioma: Español")
        self.assertEqual(details.metadata["update_strategy"], "only_fetch_once")
        self.assertEqual((chapters[0].source_id, chapters[0].number), ("https://hentaimode.com/leer/10", 1.0))
        with self.assertRaisesRegex(ValueError, "3 caracteres"):
            await source.search("ab")
        with self.assertRaisesRegex(ValueError, "URL no compatible"):
            await source.search("https://otro.example/g/10")

    async def test_script_pages_and_image_referer_match_kotlin(self):
        reader = """<script>const pages = [
          {page_image: "https://cdn/1.jpg"}, {"page_image": "https://cdn/2.webp"},
        ];</script>"""
        fetcher = Fetcher([
            Response("https://hentaimode.com/leer/10", reader),
            Response("https://cdn/1.jpg", content=b"jpeg", headers={"Content-Type": "image/jpeg"}),
        ])
        source = source_class()(fetcher)

        pages = await source.pages("https://hentaimode.com/leer/10")
        content = await source.page_bytes(pages[0])

        self.assertEqual([page.source_id for page in pages], ["https://cdn/1.jpg", "https://cdn/2.webp"])
        self.assertEqual(fetcher.requests[1][2]["headers"]["Referer"], "https://hentaimode.com/")
        self.assertEqual(b"".join(content.chunks), b"jpeg")


if __name__ == "__main__":
    unittest.main()
