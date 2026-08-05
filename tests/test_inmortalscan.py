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
    module = root.parent / "extensions-source-main" / "src" / "es" / "inmortalscan"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_madara(module, build)
    assert config is not None
    bundle = _madara_bundle((root / "engines" / "madara.py").read_text(encoding="utf-8"), config)
    namespace = {"__name__": "test_inmortalscan_bundle"}
    exec(compile(bundle, "inmortalscan_es.py", "exec"), namespace)
    return namespace["SOURCE"]


class InmortalScanTest(unittest.IsolatedAsyncioTestCase):
    async def test_madara_configuration_and_abbreviated_spanish_date(self):
        listing = """
        <div class="page-item-detail"><div class="post-title"><a href="/mg/gato/">Gato</a></div>
          <img src="/gato.jpg"></div>
        """
        holder = '<div id="manga-chapters-holder-7" data-id="7"></div>'
        chapters = """
        <li class="wp-manga-chapter"><a href="/mg/gato/capitulo-2/">Capítulo 2</a>
          <span class="chapter-release-date">ago. 04, 2026</span></li>
        """
        fetcher = Fetcher([
            Response("https://scanimnortal.com/mg/page/2/", listing),
            Response("https://scanimnortal.com/mg/gato/", holder),
            Response("https://scanimnortal.com/mg/gato/ajax/chapters", chapters),
        ])
        source = source_class()(fetcher)

        popular = await source.browse("popular", 2)
        chapter = (await source.chapters(popular[0]))[0]

        self.assertEqual(popular[0].title, "Gato")
        self.assertEqual(fetcher.requests[0][0:2], ("GET", "https://scanimnortal.com/mg/page/2/"))
        self.assertEqual(fetcher.requests[-1][0:2], (
            "POST", "https://scanimnortal.com/mg/gato/ajax/chapters",
        ))
        self.assertEqual(chapter.uploaded_at, "2026-08-04T00:00:00")
        self.assertEqual((source.manga_substring, source.load_more), ("mg", "never"))
        self.assertEqual((source.date_format, source.date_locale), ("MMM dd, yyyy", "es"))
        self.assertTrue(source.use_new_chapter_endpoint)
        self.assertEqual(source.capabilities.content_warning, "safe")


if __name__ == "__main__":
    unittest.main()
