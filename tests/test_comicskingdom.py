from __future__ import annotations

import json
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

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
    path = Path(__file__).parents[1] / "engines" / "manual" / "comicskingdom_es.py"
    namespace = {"__name__": "test_comicskingdom_bundle"}
    exec(compile(_manual_bundle(path), str(path), "exec"), namespace)
    return namespace["SOURCE"]


def manga():
    return {
        "id": 42,
        "link": "https://wp.comicskingdom.com/gato/",
        "title": {"rendered": "Gato"},
        "content": {"rendered": "Descripcion"},
        "meta": {"ck_byline_on_app": "By Ana"},
        "yoast_head": 'thumbnailUrl":"https://cdn/gato.jpg","datePublished',
    }


class ComicsKingdomTest(unittest.IsolatedAsyncioTestCase):
    async def test_wordpress_search_uses_language_order_and_genres(self):
        fetcher = Fetcher([Response("https://wp.comicskingdom.com/wp-json/wp/v2/ck_feature", [manga()])])
        source = source_class()(fetcher)

        result = await source.search("gato", 2, {
            "orderby": "modified",
            "genres": {"comedy": "include", "crime": "exclude"},
        })

        params = fetcher.requests[0][2]["params"]
        self.assertEqual(params["ck_language"], "spanish")
        self.assertEqual(params["orderby"], "modified")
        self.assertEqual(params["ck_genre"], "comedy")
        self.assertEqual(params["ck_genre_exclude"], "crime")
        self.assertEqual(result["items"][0].cover_url, "https://cdn/gato.jpg")
        self.assertIn("/ck_feature/42?", result["items"][0].source_id)

    async def test_compact_chapters_are_reversed_ranges_and_expand_to_pages(self):
        fetcher = Fetcher([
            Response("https://wp.comicskingdom.com/wp-json/wp/v2/ck_feature/42", manga()),
            Response("https://wp.comicskingdom.com/gato/", 'x "totalItems":250 y'),
            Response("https://wp.comicskingdom.com/wp-json/wp/v2/ck_comic", [
                {"assets": {"single": {"url": "https://cdn/201.jpg"}}},
                {"assets": {"single": {"url": "https://cdn/202.jpg"}}},
            ]),
        ])
        source = source_class()(fetcher)

        chapters = await source.chapters("https://wp.comicskingdom.com/wp-json/wp/v2/ck_feature/42")
        pages = await source.pages(chapters[0])

        self.assertEqual([item.title for item in chapters], ["201-250", "101-200", "1-100"])
        self.assertEqual(parse_qs(urlparse(chapters[0].source_id).query)["page"], ["3"])
        self.assertEqual([item.source_id for item in pages], ["https://cdn/201.jpg", "https://cdn/202.jpg"])

    async def test_uncompact_mode_keeps_each_dated_delivery(self):
        delivery = {
            "id": 7, "date": "2026-07-03T09:30:00",
            "link": "https://wp.comicskingdom.com/gato/2026-07-03/",
            "assets": {"single": {"url": "https://cdn/day.jpg"}},
        }
        fetcher = Fetcher([
            Response("https://wp.comicskingdom.com/wp-json/wp/v2/ck_feature/42", manga()),
            Response("https://wp.comicskingdom.com/wp-json/wp/v2/ck_comic", [delivery]),
            Response("https://wp.comicskingdom.com/wp-json/wp/v2/ck_comic/7", delivery),
        ])
        source = source_class()(fetcher)
        source.preferences = {"compactPref": False}

        chapters = await source.chapters("https://wp.comicskingdom.com/wp-json/wp/v2/ck_feature/42")
        pages = await source.pages(chapters[0])

        self.assertEqual(chapters[0].title, "2026-07-03")
        self.assertEqual(chapters[0].uploaded_at, "2026-07-03T09:30:00")
        self.assertEqual(pages[0].source_id, "https://cdn/day.jpg")


if __name__ == "__main__":
    unittest.main()
