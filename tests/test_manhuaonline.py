from __future__ import annotations

import unittest
from pathlib import Path

from tools.generate import _madara_bundle, _supported_madara


class Response:
    def __init__(self, url, text=""):
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
    module = root.parent / "extensions-source-main" / "src" / "es" / "manhuaonline"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_madara(module, build)
    assert config is not None
    bundle = _madara_bundle((root / "engines" / "madara.py").read_text(encoding="utf-8"), config)
    namespace = {"__name__": "test_manhuaonline_bundle"}
    exec(compile(bundle, "manhuaonline_es.py", "exec"), namespace)
    return namespace["SOURCE"]


class SamuraiScanTest(unittest.IsolatedAsyncioTestCase):
    async def test_native_redirects_and_locked_reader(self):
        listing = '''<div class="page-item-detail manga"><div class="item-thumb">
          <a href="/son/samurai/" title="Samurai"><img src="/samurai.jpg"></a></div>
          <div class="post-title"><a href="/son/samurai/">Samurai</a></div></div>'''
        locked = '''<div class="reading-content"><div class="mcl-locker-overlay">Chapter Locked</div></div>
          <div class="related-reading-wrap"><img src="/not-a-page.jpg"></div>'''
        fetcher = Fetcher([
            Response("https://samurai.j5z.xyz/leer/", listing),
            Response("https://samurai.j5z.xyz/son/samurai/capitulo-1/?style=list", locked),
        ])
        source = source_class()(fetcher)

        manga = (await source.browse("popular"))[0]
        pages = await source.pages("/son/samurai/capitulo-1/?style=list")

        self.assertEqual((manga.title, manga.cover_url), ("Samurai", "https://samurai.j5z.xyz/samurai.jpg"))
        self.assertEqual(pages, [])
        self.assertTrue(all(request[2]["follow_redirects"] for request in fetcher.requests))
        self.assertEqual((source.manga_substring, source.requests_per_minute), ("leer", 180))
        self.assertEqual((source.date_format, source.capabilities.content_warning), ("dd MMMM, yyyy", "safe"))


if __name__ == "__main__":
    unittest.main()
