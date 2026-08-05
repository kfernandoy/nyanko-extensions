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
    path = Path(__file__).parents[1] / "engines" / "manual" / "comicklive_es.py"
    namespace = {"__name__": "test_comicklive_bundle"}
    exec(compile(_manual_bundle(path), str(path), "exec"), namespace)
    return namespace["SOURCE"]


class ComickTest(unittest.IsolatedAsyncioTestCase):
    async def test_dynamic_filters_and_cursor_search_match_api(self):
        fetcher = Fetcher([
            Response("https://comick.live/api/metadata", {
                "genres": [{"name": "Accion", "slug": "action"}],
                "tags": [{"name": "Viajes", "slug": "travel"}],
            }),
            Response("https://comick.live/api/search", {
                "data": [{"slug": "gato", "title": "Gato", "default_thumbnail": "https://cdn/gato.jpg"}],
                "next_cursor": "cursor-2",
            }),
            Response("https://comick.live/api/search", {"data": [], "next_cursor": None}),
        ])
        source = source_class()(fetcher)

        filters = await source.get_filters()
        first = await source.search("gato", filters={
            "genres": {"action": "include"}, "tags": {"travel": "exclude"},
            "demographic": ["1"], "minimum": "2",
        })
        second = await source.search("gato", page=2)

        self.assertEqual(next(item for item in filters if item.id == "genres").options, [("action", "Accion")])
        self.assertEqual(first["items"][0].source_id, "gato")
        self.assertTrue(first["has_more"])
        params = fetcher.requests[1][2]["params"]
        self.assertIn(("genres", "action"), params)
        self.assertIn(("excluded_tags", "travel"), params)
        self.assertIn(("demographic", "1"), params)
        self.assertIn(("minimum", "2"), params)
        self.assertIn(("cursor", "cursor-2"), fetcher.requests[2][2]["params"])
        self.assertFalse(second["has_more"])

    async def test_popular_chapter_pagination_and_reader_data(self):
        chapter = {
            "hid": "abc", "chap": "12.5", "vol": "2", "lang": "es", "title": "Final",
            "created_at": "2026-01-02T03:04:05.000000Z", "group_name": ["Equipo"],
        }
        reader = '<script id="sv-data" type="application/json">{"chapter":{"images":[{"url":"https://cdn/1.webp"}]}}</script>'
        fetcher = Fetcher([
            Response("https://comick.live/api/comics/top", {
                "data": [{"slug": "gato", "title": "Gato", "default_thumbnail": "https://cdn/gato.jpg"}],
            }),
            Response("https://comick.live/api/comics/gato/chapter-list", {
                "data": [], "pagination": {"current_page": 1, "last_page": 2},
            }),
            Response("https://comick.live/api/comics/gato/chapter-list", {
                "data": [chapter], "pagination": {"current_page": 2, "last_page": 2},
            }),
            Response("https://comick.live/comic/gato/abc-chapter-12.5-es", reader),
        ])
        source = source_class()(fetcher)

        popular = await source.browse("popular", 4)
        chapters = await source.chapters("gato")
        pages = await source.pages(chapters[0])

        self.assertEqual(fetcher.requests[0][2]["params"], {"days": "7", "type": "most_follow_new"})
        self.assertTrue(popular["has_more"])
        self.assertEqual(fetcher.requests[2][2]["params"], {"lang": "es", "page": "2"})
        self.assertEqual(chapters[0].title, "Vol. 2 Ch. 12.5: Final")
        self.assertEqual(chapters[0].scanlator, "Equipo")
        self.assertEqual(pages[0].source_id, "https://cdn/1.webp")


if __name__ == "__main__":
    unittest.main()
