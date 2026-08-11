from __future__ import annotations

import unittest
from pathlib import Path

from tools.generate import _mangathemesia_bundle, _supported_mangathemesia


class Response:
    def __init__(self, url, text):
        self.url, self.text, self.status_code = url, text, 200

    def raise_for_status(self):
        pass


class Fetcher:
    def __init__(self, response):
        self.response = response

    async def request(self, method, url, **kwargs):
        return self.response


def source_class():
    root = Path(__file__).parents[1]
    module = root.parent / "extensions-source-main" / "src" / "es" / "mangashiina"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_mangathemesia(module, build)
    assert config is not None
    bundle = _mangathemesia_bundle(
        (root / "engines" / "base.py").read_text(encoding="utf-8"),
        (root / "engines" / "mangathemesia.py").read_text(encoding="utf-8"),
        config,
    )
    namespace = {"__name__": "test_mangashiina_bundle"}
    exec(compile(bundle, "mangashiina_es.py", "exec"), namespace)
    return namespace["SOURCE"]


class MangaMukaiTest(unittest.IsolatedAsyncioTestCase):
    async def test_spanish_month_first_date_and_metadata(self):
        html = '''<div id="chapterlist"><li><div class="chapternum">
          <a href="/manga/gato/capitulo-2/">Capítulo 2</a></div>
          <span class="chapterdate">agosto 5, 2026</span></li></div>'''
        source = source_class()(Fetcher(Response("https://mangamukai.com/manga/gato/", html)))

        chapter = (await source.chapters("/manga/gato/"))[0]

        self.assertEqual(chapter.uploaded_at, "2026-08-05T00:00:00")
        self.assertEqual((source.date_format, source.date_locale), ("MMMM dd, yyyy", "es"))
        self.assertEqual(source.capabilities.content_warning, "mixed")


if __name__ == "__main__":
    unittest.main()
