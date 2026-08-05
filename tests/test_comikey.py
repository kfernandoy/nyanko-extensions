from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.generate import _manual_bundle


class Response:
    def __init__(self, url, payload):
        self.url, self.status_code = url, 200
        self.text = payload if isinstance(payload, str) else json.dumps(payload)

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        pass


class Fetcher:
    def __init__(self, responses):
        self.responses, self.requests = responses, []

    async def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return self.responses.pop(0)


def source_class():
    path = Path(__file__).parents[1] / "engines" / "manual" / "comikey_es.py"
    namespace = {"__name__": "test_comikey_bundle"}
    exec(compile(_manual_bundle(path), str(path), "exec"), namespace)
    return namespace["SOURCE"]


class ComikeyTest(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_uses_comikey_list_and_filters(self):
        listing = '''<div class="series-listing" data-view="list"><ul><li>
            <div class="image"><picture><img src="/gato.jpg"></picture></div>
            <div class="series-data"><span class="title"><a href="/comics/gato/123/">Gato</a></span></div>
            <div class="excerpt"><p>Resumen</p></div><div class="desc"><p>Descripcion</p></div>
            <ul class="category-listing"><li><a>Accion</a></li></ul>
        </li></ul></div><ul class="pagination"><li class="next-page"><a>Next</a></li></ul>'''
        fetcher = Fetcher([Response("https://comikey.com/comics/", listing)])
        source = source_class()(fetcher)

        result = await source.search("ga", 2, {"order": "name", "direction": "asc", "filter": "manga"})

        self.assertTrue(source.capabilities.requires_webview)
        self.assertEqual(fetcher.requests[0][2]["params"], {"order": "name", "page": "2", "q": "ga", "filter": "manga"})
        self.assertEqual(result["items"][0].description, "Resumen\n\nDescripcion")
        self.assertEqual(result["items"][0].content_tags, ("Accion",))
        self.assertTrue(result["has_more"])

    async def test_episode_api_keeps_locked_by_default_and_uses_spanish_slug(self):
        comic = {
            "link": "/comics/gato/123/", "name": "Gato", "author": [], "artist": [],
            "tags": [], "description": "", "excerpt": "", "format": 1,
            "full_cover": "/cover.jpg", "update_status": 4, "update_text": "",
        }
        html = f'''<script id="comic" type="application/json">{json.dumps(comic)}</script>
            <script>GUNDAM.token = "secret";</script>'''
        episodes = {"episodes": [
            {"id": "e4p-one", "number": 1.0, "title": "Uno", "subtitle": None,
             "releasedAt": "2025-01-01T00:00:00Z", "finalPrice": 0, "owned": False},
            {"id": "e4p-two", "number": 2.5, "title": "Dos", "subtitle": "Fin",
             "releasedAt": "2025-02-01T00:00:00Z", "finalPrice": 10, "owned": False},
            {"id": "e4p-future", "number": 3.0, "title": "Futuro", "subtitle": None,
             "releasedAt": "2099-01-01T00:00:00Z", "finalPrice": 0, "owned": False},
        ]}
        fetcher = Fetcher([
            Response("https://comikey.com/comics/gato/123/", html),
            Response("https://gundam.comikey.net/comic/123/episodes", episodes),
        ])
        source = source_class()(fetcher)

        chapters = await source.chapters("https://comikey.com/comics/gato/123/")

        self.assertEqual(fetcher.requests[1][2]["params"], {"language": "es", "token": "secret"})
        self.assertEqual([item.title for item in chapters], ["Dos: Fin", "Uno"])
        self.assertTrue(chapters[0].source_id.endswith("/two/capitulo-espanol-2-5/"))

    async def test_direct_manifest_uses_smaller_webp_and_access_token(self):
        reader = '<script id="lmao-init">{"manifest":"https://relay.epub.rocks/book/manifest","act":"app-token"}</script>'
        manifest = {
            "metadata": {"readingProgression": "ttb"},
            "readingOrder": [{
                "href": "original.jpg", "type": "image/jpeg", "height": 2048, "width": 900,
                "alternate": [{"href": "small.webp", "type": "image/webp", "height": 1800, "width": 1200}],
            }],
        }
        fetcher = Fetcher([
            Response("https://comikey.com/read/gato/one/capitulo-espanol-1/", reader),
            Response("https://cdn.epub.rocks/book/manifest", manifest),
        ])
        source = source_class()(fetcher)

        pages = await source.pages("https://comikey.com/read/gato/one/capitulo-espanol-1/")

        self.assertEqual(fetcher.requests[1][1], "https://relay.epub.rocks/book/manifest")
        self.assertEqual(pages[0].source_id, "https://cdn.epub.rocks/book/small.webp?act=app-token")
        self.assertEqual(len(fetcher.requests[0][2]["headers"]["X-Requested-With"]), 14)


if __name__ == "__main__":
    unittest.main()
