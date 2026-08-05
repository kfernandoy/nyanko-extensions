from __future__ import annotations

import unittest
from datetime import datetime, timedelta
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
    module = root.parent / "extensions-source-main" / "src" / "es" / "gremorymangas"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_madara(module, build)
    assert config is not None
    bundle = _madara_bundle((root / "engines" / "madara.py").read_text(encoding="utf-8"), config)
    namespace = {"__name__": "test_gremorymangas_bundle"}
    exec(compile(bundle, "gremorymangas_es.py", "exec"), namespace)
    return namespace["SOURCE"]


class GremoryMangasTest(unittest.IsolatedAsyncioTestCase):
    async def test_new_endpoint_xhr_and_spanish_chapter_dates_match_kotlin(self):
        series_html = '<div id="manga-chapters-holder-7" data-id="7"></div>'
        chapters_html = """
        <ul>
          <li class="wp-manga-chapter">
            <a href="/manga/gato/capitulo-1">Capitulo 1</a>
            <img alt="hace 2 días"><span class="chapter-release-date">enero 01, 2000</span>
          </li>
          <li class="wp-manga-chapter">
            <a href="/manga/gato/capitulo-2">Capitulo 2</a>
            <span class="chapter-release-date">agosto 04, 2026</span>
          </li>
        </ul>
        """
        fetcher = Fetcher([
            Response("https://gremoryhistorias.org/manga/gato", series_html),
            Response("https://gremoryhistorias.org/manga/gato/ajax/chapters", chapters_html),
        ])
        source = source_class()(fetcher)

        chapters = await source.chapters("https://gremoryhistorias.org/manga/gato")

        self.assertEqual(fetcher.requests[1][0:2], (
            "POST", "https://gremoryhistorias.org/manga/gato/ajax/chapters",
        ))
        self.assertEqual(fetcher.requests[1][2]["headers"], {"X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(source.date_format, "MMMM dd, yyyy")
        self.assertEqual(source.date_locale, "es")
        self.assertEqual(chapters[1].uploaded_at, "2026-08-04T00:00:00")
        relative = datetime.fromisoformat(chapters[0].uploaded_at)
        self.assertLess(abs(relative - (datetime.now().replace(microsecond=0) - timedelta(days=2))), timedelta(seconds=2))
        self.assertEqual(chapters[0].language, "es")
        self.assertTrue(chapters[0].source_id.endswith("?style=list"))
        self.assertEqual(source.capabilities.content_warning, "safe")


if __name__ == "__main__":
    unittest.main()
