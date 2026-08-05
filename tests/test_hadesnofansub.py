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
    module = root.parent / "extensions-source-main" / "src" / "es" / "hadesnofansub"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_madara(module, build)
    assert config is not None
    bundle = _madara_bundle((root / "engines" / "madara.py").read_text(encoding="utf-8"), config)
    namespace = {"__name__": "test_hadesnofansub_bundle"}
    exec(compile(bundle, "hadesnofansub_es.py", "exec"), namespace)
    return namespace["SOURCE"]


class HadesNoFansubTest(unittest.IsolatedAsyncioTestCase):
    async def test_tmo_archive_never_uses_load_more(self):
        archive = """
        <div class="page-item-detail"><div class="post-title">
          <a href="/tmo/gato">Gato</a>
        </div><img src="/gato.jpg"></div>
        """
        fetcher = Fetcher([Response("https://lectorhades.latamtoon.com/tmo/page/2/", archive)])
        source = source_class()(fetcher)

        popular = await source.browse("popular", 2)

        self.assertEqual(fetcher.requests[0][0:2], (
            "GET", "https://lectorhades.latamtoon.com/tmo/page/2/",
        ))
        self.assertEqual(fetcher.requests[0][2]["params"], {"m_orderby": "views"})
        self.assertEqual(popular[0].title, "Gato")
        self.assertEqual(source.load_more, "never")
        self.assertEqual(source.capabilities.content_warning, "mixed")

    async def test_custom_status_ignores_scanlator_tag_and_parses_us_style_date(self):
        details = """
        <div class="post-title"><h1>Gato</h1></div>
        <div class="summary_image"><img src="/cover.jpg"></div>
        <div class="author-content"><a>Ana</a></div>
        <div class="artist-content"><a>Leo</a></div>
        <div class="description-summary"><div class="summary__content"><p>Uno</p><p>Dos</p></div></div>
        <div class="summary_content"><div class="post-content">
          <div class="post-content_item"><div class="summary-heading">Status</div><div class="summary-content">En Curso</div></div>
          <div class="post-content_item"><div class="summary-heading">Type</div><div class="summary-content">Manhwa</div></div>
          <div class="post-content_item"><div class="summary-heading">Alt</div><div class="summary-content">Cat</div></div>
        </div></div>
        <div class="genres-content"><a>Acción</a></div>
        <div class="tags-content"><a class="notUsed">Hades Scan</a></div>
        """
        series_page = '<div id="manga-chapters-holder-9" data-id="9"></div>'
        chapter_list = """
        <li class="wp-manga-chapter"><a href="/tmo/gato/capitulo-1">Capitulo 1</a>
          <span class="chapter-release-date">08/04/2026</span>
        </li>
        """
        fetcher = Fetcher([
            Response("https://lectorhades.latamtoon.com/tmo/gato", details),
            Response("https://lectorhades.latamtoon.com/tmo/gato", series_page),
            Response("https://lectorhades.latamtoon.com/tmo/gato/ajax/chapters", chapter_list),
        ])
        source = source_class()(fetcher)

        manga = await source.details("https://lectorhades.latamtoon.com/tmo/gato")
        chapters = await source.chapters("https://lectorhades.latamtoon.com/tmo/gato")

        self.assertEqual((manga.author, manga.artist, manga.status), ("Ana", "Leo", "ongoing"))
        self.assertEqual(manga.description, "Uno\n\nDos\n\nAlternative name(s): Cat")
        self.assertEqual(manga.content_tags, ("Acción", "Manhwa"))
        self.assertNotIn("Hades Scan", manga.content_tags)
        self.assertEqual(chapters[0].uploaded_at, "2026-08-04T00:00:00")
        self.assertEqual(fetcher.requests[2][2]["headers"], {"X-Requested-With": "XMLHttpRequest"})
        self.assertEqual((source.date_format, source.date_locale), ("MM/dd/yyyy", "es"))


if __name__ == "__main__":
    unittest.main()
