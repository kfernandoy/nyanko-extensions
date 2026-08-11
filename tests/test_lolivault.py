from __future__ import annotations

import unittest
from pathlib import Path

from tools.generate import _foolslide_bundle, _supported_foolslide


class Response:
    def __init__(self, url: str, text: str):
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
    module = root.parent / "extensions-source-main" / "src" / "es" / "lolivault"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_foolslide(module, build)
    assert config is not None
    bundle = _foolslide_bundle(
        (root / "engines" / "base.py").read_text(encoding="utf-8"),
        (root / "engines" / "foolslide.py").read_text(encoding="utf-8"),
        config,
    )
    namespace = {"__name__": "test_lolivault_bundle"}
    exec(compile(bundle, "lolivault_es.py", "exec"), namespace)
    return namespace["SOURCE"]


class LolivaultTest(unittest.IsolatedAsyncioTestCase):
    async def test_foolslide_catalog_search_and_metadata(self):
        listing = """
        <div class="group"><a title="Gato" href="/series/gato/">Gato</a>
          <img src="/covers/thumb_gato.jpg"></div>
        """
        fetcher = Fetcher([
            Response("https://lector.lolivault.net/directory/1/", listing),
            Response("https://lector.lolivault.net/latest/1/", listing),
            Response("https://lector.lolivault.net/search/", listing),
        ])
        source = source_class()(fetcher)

        popular = await source.browse("popular")
        latest = await source.browse("latest")
        found = await source.search("gato")

        self.assertEqual((popular[0].title, latest[0].title, found[0].title), ("Gato", "Gato", "Gato"))
        self.assertEqual(popular[0].cover_url, "https://lector.lolivault.net/covers/gato.jpg")
        self.assertEqual(fetcher.requests[-1], (
            "POST", "https://lector.lolivault.net/search/", {"data": {"search": "gato"}},
        ))
        self.assertEqual(source.get_preferences()[0].default, True)
        self.assertEqual(source.capabilities.content_warning, "nsfw")

    async def test_details_chapters_dates_and_pages(self):
        details = """
        <div class="thumbnail"><img src="/cover.jpg"></div><div class="info">
          <b>Author</b>: Ana<br><b>Artist</b>: Beto<br><b>Synopsis</b>: Sinopsis<br></div>
        """
        chapters = """
        <div class="group"><div class="element"><a title="Capítulo 2" href="/read/gato/2/">Capítulo 2</a>
          <div class="meta_r">Subido, 2026.08.05</div></div></div>
        """
        pages = 'var pages = [{"url":"/pages/1.jpg"},{"url":"https://cdn/pages/2.jpg"}];'
        fetcher = Fetcher([
            Response("https://lector.lolivault.net/series/gato/", details),
            Response("https://lector.lolivault.net/series/gato/", chapters),
            Response("https://lector.lolivault.net/read/gato/2/", pages),
        ])
        source = source_class()(fetcher)

        manga = await source.details("https://lector.lolivault.net/series/gato/")
        chapter = (await source.chapters(manga))[0]
        page_list = await source.pages(chapter)

        self.assertEqual((manga.author, manga.artist, manga.description), ("Ana", "Beto", "Sinopsis"))
        self.assertEqual((chapter.number, chapter.uploaded_at), (2.0, "2026-08-05T00:00:00"))
        self.assertEqual([page.source_id for page in page_list], [
            "https://lector.lolivault.net/pages/1.jpg", "https://cdn/pages/2.jpg",
        ])


if __name__ == "__main__":
    unittest.main()
