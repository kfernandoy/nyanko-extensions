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
    module = root.parent / "extensions-source-main" / "src" / "es" / "inventariooculto"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_madara(module, build)
    assert config is not None
    bundle = _madara_bundle((root / "engines" / "madara.py").read_text(encoding="utf-8"), config)
    namespace = {"__name__": "test_inventariooculto_bundle"}
    exec(compile(bundle, "inventariooculto_es.py", "exec"), namespace)
    return namespace["SOURCE"]


class InventarioOcultoTest(unittest.IsolatedAsyncioTestCase):
    async def test_new_chapter_endpoint_and_spanish_long_date(self):
        holder = '<div id="manga-chapters-holder-8" data-id="8"></div>'
        chapters = """
        <li class="wp-manga-chapter"><a href="/manga/gato/capitulo-2/">Capítulo 2</a>
          <span class="chapter-release-date">04 agosto, 2026</span></li>
        """
        fetcher = Fetcher([
            Response("https://inventariooculto.com/manga/gato/", holder),
            Response("https://inventariooculto.com/manga/gato/ajax/chapters", chapters),
        ])
        source = source_class()(fetcher)

        chapter = (await source.chapters("/manga/gato/"))[0]

        self.assertEqual(fetcher.requests[-1][0:2], (
            "POST", "https://inventariooculto.com/manga/gato/ajax/chapters",
        ))
        self.assertEqual(chapter.uploaded_at, "2026-08-04T00:00:00")
        self.assertEqual((source.date_format, source.date_locale), ("dd MMMM, yyyy", "es"))
        self.assertTrue(source.use_new_chapter_endpoint)
        self.assertEqual(source.capabilities.content_warning, "safe")


if __name__ == "__main__":
    unittest.main()
