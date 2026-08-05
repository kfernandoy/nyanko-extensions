from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.generate import _manual_bundle


class Response:
    def __init__(self, url, payload):
        self.url, self.status_code = url, 200
        self.text = payload if isinstance(payload, str) else json.dumps(payload)

    def raise_for_status(self):
        pass


class Fetcher:
    def __init__(self, responses):
        self.responses, self.requests = responses, []

    async def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return self.responses.pop(0)


def source_class():
    path = Path(__file__).parents[1] / "engines" / "manual" / "doujinhentai_es.py"
    namespace = {"__name__": "test_doujinhentai_bundle"}
    exec(compile(_manual_bundle(path), str(path), "exec"), namespace)
    return namespace["SOURCE"]


class DoujinHentaiTest(unittest.IsolatedAsyncioTestCase):
    async def test_listing_and_route_filter_precedence(self):
        listing = '''<div class="group bg-white rounded-2xl"><section>
            <a class="block" href="/obra/gato"><h3 class="font-bold">Gato</h3><img data-src="/gato.jpg"></a>
        </section></div><a rel="next" href="?page=3">Next</a>'''
        fetcher = Fetcher([Response("https://doujinhentai.net/lista-manga-hentai/category/ahegao", listing)])
        source = source_class()(fetcher)

        result = await source.search("", 2, {"genre": "ahegao", "artist": "Ignorado", "sort": "views"})

        self.assertEqual(fetcher.requests[0][1], "https://doujinhentai.net/lista-manga-hentai/category/ahegao")
        self.assertEqual(fetcher.requests[0][2]["params"], {"page": "2"})
        self.assertEqual(result["items"][0].cover_url, "https://doujinhentai.net/gato.jpg")
        self.assertTrue(result["has_more"])
        self.assertEqual(source.capabilities.content_warning, "nsfw")

    async def test_query_ignores_filters_and_chapter_metadata_is_preserved(self):
        empty = "<html></html>"
        chapters_html = '''<div class="flex items-center gap-4 p-3 mb-2 border rounded-lg">
            <div class="flex-1"><a class="font-bold" href="/roman">Leer Capitulo 12.5</a>
                <div class="text-sm font-medium">Desenlace</div></div>
            <div class="text-sm text-right"><a href="/user/Neko">Neko</a>
                <span class="font-medium">3 Jan. 2026</span></div>
        </div>'''
        fetcher = Fetcher([
            Response("https://doujinhentai.net/lista-manga-hentai", empty),
            Response("https://doujinhentai.net/obra/gato", chapters_html),
        ])
        source = source_class()(fetcher)

        await source.search("gato", 3, {"genre": "ahegao"})
        chapters = await source.chapters("https://doujinhentai.net/obra/gato")

        self.assertEqual(fetcher.requests[0][2]["params"], {"page": "3", "search": "gato"})
        self.assertEqual(chapters[0].source_id, "https://doujinhentai.net/roman")
        self.assertEqual(chapters[0].title, "Capitulo 12.5: Desenlace")
        self.assertEqual(chapters[0].number, 12.5)
        self.assertEqual(chapters[0].scanlator, "Neko")
        self.assertEqual(chapters[0].uploaded_at, "2026-01-03T00:00:00")

    async def test_embedded_page_map_is_sorted_and_unescaped(self):
        reader = '''<script>const pageUrls = {"2":"https:\\/\\/cdn\\/2.jpg","1":"https:\\/\\/cdn\\/1.jpg"};</script>
            <div id="vertical-pages-container"><div data-page="1"><img src="https://cdn/fallback.jpg"></div></div>'''
        fetcher = Fetcher([Response("https://doujinhentai.net/roman", reader)])
        source = source_class()(fetcher)

        pages = await source.pages("https://doujinhentai.net/roman")

        self.assertEqual([page.source_id for page in pages], ["https://cdn/1.jpg", "https://cdn/2.jpg"])


if __name__ == "__main__":
    unittest.main()
