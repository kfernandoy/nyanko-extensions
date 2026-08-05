from __future__ import annotations

import unittest
from pathlib import Path

from tools.generate import _madara_bundle, _supported_madara


class Response:
    def __init__(self, url, text="", payload=None, content=b"", headers=None):
        self.url, self.text, self.payload = url, text, payload
        self.content, self.headers, self.status_code = content, headers or {}, 200

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class Fetcher:
    def __init__(self, responses):
        self.responses, self.requests = responses, []

    async def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return self.responses.pop(0)


def source_class():
    root = Path(__file__).parents[1]
    module = root.parent / "extensions-source-main" / "src" / "es" / "mangacrab"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_madara(module, build)
    assert config is not None
    bundle = _madara_bundle((root / "engines" / "madara.py").read_text(encoding="utf-8"), config)
    namespace = {"__name__": "test_mangacrab_bundle"}
    exec(compile(bundle, "mangacrab_es.py", "exec"), namespace)
    return namespace["SOURCE"]


class MangaCrabTest(unittest.IsolatedAsyncioTestCase):
    async def test_custom_listing_paginated_chapters_and_node_header(self):
        popular = '<div class="mv-rank-panel" data-panel="monthly"><div class="mv-rank-item"><a href="/series/gato/"><img data-src="/gato.jpg"><span class="mv-rank-title">Gato</span></a></div></div>'
        series = '<div id="mv-chapter-list" data-manga-id="7"></div><script>var mvTheme = {"nonce":"abc"};</script>'
        chapter_html = '<article class="chapter-item"><div><a href="/series/gato/capitulo-2/">Capítulo 2</a></div></article>'
        reader = '<script>window.x={"imgHeader":"secret"}</script><div id="mv-reader-body"><img class="mv-secure-img" data-sec-src="https://cdn.example/2.jpg"></div>'
        fetcher = Fetcher([
            Response("https://mangacrab.org", popular),
            Response("https://mangacrab.org/series/gato/", series),
            Response("https://mangacrab.org/wp-admin/admin-ajax.php", payload={"success": True, "data": {"list": chapter_html}}),
            Response("https://mangacrab.org/wp-admin/admin-ajax.php", payload={"success": True, "data": {"list": ""}}),
            Response("https://mangacrab.org/series/gato/capitulo-2/", reader),
            Response("https://cdn.example/2.jpg", content=b"image", headers={"Content-Type": "image/jpeg"}),
        ])
        source = source_class()(fetcher)

        manga = (await source.browse("popular"))[0]
        chapter = (await source.chapters(manga))[0]
        page = (await source.pages(chapter))[0]
        content = await source.page_bytes(page)

        self.assertEqual((manga.title, manga.cover_url), ("Gato", "https://mangacrab.org/gato.jpg"))
        self.assertEqual((chapter.title, chapter.number), ("Capítulo 2", 2.0))
        self.assertEqual(page.source_id, "https://cdn.example/2.jpg#nodeHeader=secret")
        self.assertEqual(fetcher.requests[-1][2]["headers"]["Node"], "secret")
        self.assertEqual(b"".join(content.chunks), b"image")
        self.assertEqual((source.requests_per_minute, source.date_format), (300, "dd/MM/yyyy"))
        self.assertEqual(source.capabilities.content_warning, "safe")


if __name__ == "__main__":
    unittest.main()
