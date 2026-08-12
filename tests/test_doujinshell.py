from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.generate import _manual_bundle


class Response:
    def __init__(self, url, payload, status=200):
        self.url, self.status_code = url, status
        self.text = payload if isinstance(payload, str) else json.dumps(payload)

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
    bundle = _manual_bundle(
        root / "engines" / "manual" / "doujinshell_es.py",
        (root / "engines" / "madara.py").read_text(encoding="utf-8"),
    )
    namespace = {"__name__": "test_doujinshell_bundle"}
    exec(compile(bundle, "doujinshell_es.py", "exec"), namespace)
    return namespace["SOURCE"]


class DoujinsHellTest(unittest.IsolatedAsyncioTestCase):
    async def test_filters_and_paged_search_use_non_load_more_route(self):
        listing = '''<div class="c-tabs-item__content"><div class="post-title">
            <a href="/doujin/gato/">Gato</a></div></div><a rel="next">Next</a>'''
        fetcher = Fetcher([Response("https://doujinshell.net/page/2/", listing)])
        source = source_class()(fetcher)

        result = await source.search("gato", 2, {
            "author": "Ana", "status": ["end", "on-going"], "order": "latest", "adult": "1",
        })

        ids = [item.id for item in source.get_filters()]
        self.assertEqual(ids, ["author", "artist", "year", "status", "order", "adult"])
        self.assertEqual(fetcher.requests[0][1], "https://doujinshell.net/page/2/")
        params = fetcher.requests[0][2]["params"]
        self.assertIn(("status[]", "end"), params)
        self.assertIn(("status[]", "on-going"), params)
        self.assertIn(("m_orderby", "latest"), params)
        self.assertIn(("adult", "1"), params)
        self.assertEqual(result["items"][0].title, "Gato")
        self.assertTrue(result["has_more"])
        self.assertEqual(source.capabilities.content_warning, "nsfw")

    async def test_single_chapter_date_and_reader_exclusions_match_overrides(self):
        details = '''<li class="wp-manga-chapter"><a href="/ignorado">Ignorado</a></li>
            <div class="listing-chapters_wrap"><ul><li class="wp-manga-chapter">
                <a href="/doujin/gato/uno?style=paged">Entrada 7</a>
                <span class="chapter-release-date">4 agosto, 2026</span>
            </li></ul></div>'''
        reader = '''<div class="reading-content">
            <img class="aligncenter" src="https://cdn/boton.jpg"><img src="https://cdn/pagina.jpg">
        </div>'''
        fetcher = Fetcher([
            Response("https://doujinshell.net/doujin/gato/", details),
            Response("https://doujinshell.net/doujin/gato/uno?style=list", reader),
        ])
        source = source_class()(fetcher)

        chapters = await source.chapters("https://doujinshell.net/doujin/gato/")
        pages = await source.pages(chapters[0])

        self.assertEqual(len(chapters), 1)
        self.assertEqual(chapters[0].title, "Cap\u00edtulo")
        self.assertEqual(chapters[0].uploaded_at, "2026-08-04T00:00:00")
        self.assertEqual(chapters[0].source_id, "https://doujinshell.net/doujin/gato/uno?style=list")
        self.assertEqual([page.source_id for page in pages], ["https://cdn/pagina.jpg"])

    async def test_video_only_chapter_is_rejected_explicitly(self):
        fetcher = Fetcher([Response(
            "https://doujinshell.net/doujin/video",
            '<div class="reading-content"><iframe src="https://video"></iframe></div>',
        )])
        source = source_class()(fetcher)

        with self.assertRaisesRegex(ValueError, "videos"):
            await source.pages("https://doujinshell.net/doujin/video")


if __name__ == "__main__":
    unittest.main()
