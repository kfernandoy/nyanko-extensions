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
    module = root.parent / "extensions-source-main" / "src" / "es" / "kazokuden"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_madara(module, build)
    assert config is not None
    bundle = _madara_bundle((root / "engines" / "madara.py").read_text(encoding="utf-8"), config)
    namespace = {"__name__": "test_kazokuden_bundle"}
    exec(compile(bundle, "kazokuden_es.py", "exec"), namespace)
    return namespace["SOURCE"]


class KazokuDenTest(unittest.IsolatedAsyncioTestCase):
    async def test_standard_madara_details_chapters_and_pages(self):
        details = """
        <div class="post-title"><h1>Gato</h1></div><div class="summary_image"><img src="/cover.jpg"></div>
        <div class="description-summary"><div class="summary__content"><p>Sinopsis</p></div></div>
        <div class="author-content"><a>Ana</a></div><div class="artist-content"><a>Beto</a></div>
        <div class="summary_content"><div class="post-content_item"><div class="summary-heading">Estado</div>
          <div class="summary-content">Finalizado</div></div></div>
        <div class="genres-content"><a>Acción</a></div>
        """
        chapters = """
        <li class="wp-manga-chapter"><a href="/manga/gato/capitulo-2/">Capítulo 2</a>
          <span class="chapter-release-date">agosto 04, 2026</span></li>
        """
        pages = '<div class="reading-content"><img src="/pages/1.jpg"></div>'
        fetcher = Fetcher([
            Response("https://www.kazokuden.com/manga/gato/", details),
            Response("https://www.kazokuden.com/manga/gato/", chapters),
            Response("https://www.kazokuden.com/manga/gato/capitulo-2/?style=list", pages),
        ])
        source = source_class()(fetcher)

        manga = await source.details("/manga/gato/")
        chapter = (await source.chapters(manga))[0]
        page = (await source.pages(chapter))[0]

        self.assertEqual((manga.title, manga.author, manga.artist), ("Gato", "Ana", "Beto"))
        self.assertEqual((manga.status, manga.content_tags), ("completed", ("Acción",)))
        self.assertEqual(manga.description, "Sinopsis")
        self.assertEqual(chapter.uploaded_at, "2026-08-04T00:00:00")
        self.assertEqual(page.source_id, "https://www.kazokuden.com/pages/1.jpg")
        self.assertEqual((source.manga_substring, source.load_more), ("manga", "auto"))
        self.assertEqual(source.capabilities.content_warning, "safe")


if __name__ == "__main__":
    unittest.main()
