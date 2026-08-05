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
    module = root.parent / "extensions-source-main" / "src" / "es" / "infrafandub"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_madara(module, build)
    assert config is not None
    bundle = _madara_bundle((root / "engines" / "madara.py").read_text(encoding="utf-8"), config)
    namespace = {"__name__": "test_infrafandub_bundle"}
    exec(compile(bundle, "infrafandub_es.py", "exec"), namespace)
    return namespace["SOURCE"]


class InfraFandubTest(unittest.IsolatedAsyncioTestCase):
    async def test_custom_madara_selectors_and_chapter_endpoint(self):
        listing = """
        <div class="manga-item"><div class="title"><a href="/manga/gato/">Gato</a></div>
          <img src="/gato.jpg"></div>
        """
        details = """
        <main><h1 class="series-title">Gato</h1><aside class="sidebar"><img class="series-cover" src="/cover.jpg"></aside>
          <div class="series-details"><div class="detail-item">Autor <span class="detail-value">Ana</span></div>
          <div class="detail-item">Artista <span class="detail-value">Beto</span></div>
          <div class="detail-item">Estado <span class="detail-value">Completado</span></div></div>
          <div class="genres"><a class="genre-tag">Acción</a></div><div class="summary-text">Sinopsis</div>
        </main>
        """
        chapters = """
        <div class="chapters-list"><a class="chapter-item" href="/manga/gato/capitulo-2/">
          <span class="chapter-number">Capítulo 2</span><span class="chapter-date">04/08/2026</span></a></div>
        """
        fetcher = Fetcher([
            Response("https://infrafandub.com/manga/", listing),
            Response("https://infrafandub.com/?s=gato", listing),
            Response("https://infrafandub.com/manga/gato/", details),
            Response("https://infrafandub.com/manga/gato/ajax/chapters/", chapters),
        ])
        source = source_class()(fetcher)

        popular = await source.browse("popular")
        search = await source.search("gato")
        manga = await source.details(popular[0])
        chapter = (await source.chapters(manga))[0]

        self.assertEqual((popular[0].title, search[0].title), ("Gato", "Gato"))
        self.assertEqual((manga.author, manga.artist, manga.status), ("Ana", "Beto", "completed"))
        self.assertEqual((manga.description, manga.content_tags), ("Sinopsis", ("Acción",)))
        self.assertEqual(fetcher.requests[-1][0:2], (
            "POST", "https://infrafandub.com/manga/gato/ajax/chapters/",
        ))
        self.assertEqual((chapter.number, chapter.uploaded_at), (2.0, "2026-08-04T00:00:00"))
        self.assertEqual((source.date_format, source.date_locale), ("dd/MM/yyyy", "es"))
        self.assertEqual(source.requests_per_minute, 120)
        self.assertEqual(source.capabilities.content_warning, "safe")


if __name__ == "__main__":
    unittest.main()
