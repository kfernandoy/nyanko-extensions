from __future__ import annotations

import unittest
from pathlib import Path

from tools.generate import _madara_bundle, _supported_madara


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
    module = root.parent / "extensions-source-main" / "src" / "es" / "lectormangalat"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_madara(module, build)
    assert config is not None
    bundle = _madara_bundle((root / "engines" / "madara.py").read_text(encoding="utf-8"), config)
    namespace = {"__name__": "test_lectormangalat_bundle"}
    exec(compile(bundle, "lectormangalat_es.py", "exec"), namespace)
    return namespace["SOURCE"]


class LectorMangaLatTest(unittest.IsolatedAsyncioTestCase):
    async def test_madara_profile_chapter_endpoint_and_pages(self):
        listing = """
        <div class="page-item-detail"><div class="post-title"><a href="/biblioteca/gato/">Gato</a></div>
          <img data-src="/gato.jpg"></div>
        """
        series = '<div id="manga-chapters-holder-1"></div>'
        chapters = """
        <li class="wp-manga-chapter"><a href="/biblioteca/gato/capitulo-2/">Capítulo 2</a>
          <span class="chapter-release-date">agosto 04, 2026</span></li>
        """
        pages = """
        <div class="reading-content"><img src="/ad.jpg">
          <div class="page-break"><img data-src="/pages/1.jpg"></div></div>
        """
        fetcher = Fetcher([
            Response("https://lectormangass.com/biblioteca/", listing),
            Response("https://lectormangass.com/biblioteca/gato/", series),
            Response("https://lectormangass.com/biblioteca/gato/ajax/chapters", chapters),
            Response("https://lectormangass.com/biblioteca/gato/capitulo-2/?style=list", pages),
        ])
        source = source_class()(fetcher)

        manga = (await source.browse("popular"))[0]
        chapter = (await source.chapters(manga))[0]
        page_list = await source.pages(chapter)

        self.assertEqual(manga.title, "Gato")
        self.assertEqual(fetcher.requests[2][0:2], (
            "POST", "https://lectormangass.com/biblioteca/gato/ajax/chapters",
        ))
        self.assertEqual((chapter.number, chapter.uploaded_at), (2.0, "2026-08-04T00:00:00"))
        self.assertEqual([page.source_id for page in page_list], ["https://lectormangass.com/pages/1.jpg"])
        self.assertEqual((source.date_locale, source.requests_per_minute), ("es", 120))
        self.assertEqual(source.capabilities.content_warning, "mixed")


if __name__ == "__main__":
    unittest.main()
