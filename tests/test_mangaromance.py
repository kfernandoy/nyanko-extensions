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
    module = root.parent / "extensions-source-main" / "src" / "es" / "mangaromance"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_madara(module, build)
    assert config is not None
    bundle = _madara_bundle((root / "engines" / "madara.py").read_text(encoding="utf-8"), config)
    namespace = {"__name__": "test_mangaromance_bundle"}
    exec(compile(bundle, "mangaromance_es.py", "exec"), namespace)
    return namespace["SOURCE"]


class MangaRomanceTest(unittest.IsolatedAsyncioTestCase):
    async def test_typed_new_chapter_endpoint_and_spanish_date(self):
        holder = '<div id="manga-chapters-holder-8" data-id="8"></div>'
        chapters = '''<li class="wp-manga-chapter"><a href="/manga/gato/capitulo-2/">Capítulo 2</a>
          <span class="chapter-release-date">05 agosto, 2026</span></li>'''
        fetcher = Fetcher([
            Response("https://mangaromance19.com/manga/gato/", holder),
            Response("https://mangaromance19.com/manga/gato/ajax/chapters", chapters),
        ])
        source = source_class()(fetcher)

        chapter = (await source.chapters("/manga/gato/"))[0]

        self.assertEqual(fetcher.requests[-1][0:2], (
            "POST", "https://mangaromance19.com/manga/gato/ajax/chapters",
        ))
        self.assertEqual(fetcher.requests[-1][2]["headers"], {"X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(chapter.uploaded_at, "2026-08-05T00:00:00")
        self.assertTrue(source.use_new_chapter_endpoint)
        self.assertEqual(source.load_more, "never")
        self.assertEqual((source.date_format, source.date_locale), ("dd MMMM, yyyy", "es"))
        self.assertEqual(source.capabilities.content_warning, "mixed")


if __name__ == "__main__":
    unittest.main()
