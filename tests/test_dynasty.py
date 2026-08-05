from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.generate import _extract_kotlin_metadata, _generic_bundle, _supported_generic


class Response:
    def __init__(self, url, payload, status=200):
        self.url, self.payload, self.status_code = url, payload, status
        self.text = json.dumps(payload)

    def json(self):
        return self.payload

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
    module = root.parent / "extensions-source-main" / "src" / "es" / "dynasty"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_generic(module, build)
    assert config is not None
    config["content_warning"] = _extract_kotlin_metadata(module)
    bundle = _generic_bundle(
        (root / "engines" / "madara.py").read_text(encoding="utf-8"),
        (root / "engines" / "generic.py").read_text(encoding="utf-8"),
        config,
    )
    namespace = {"__name__": "test_dynasty_bundle"}
    exec(compile(bundle, "dynasty_es.py", "exec"), namespace)
    return namespace["SOURCE"]


def manga(identifier, title, kind="manga", **extra):
    return {
        "id": identifier, "slug": title.lower(), "title": title, "type": kind,
        "cover_image": f"/covers/{identifier}.jpg", "views": 0, "rating": 0,
        "updated_at": "2026-08-01T00:00:00.000Z", **extra,
    }


class DynastyTest(unittest.IsolatedAsyncioTestCase):
    async def test_filters_search_precedence_and_api_mapping_match_kotlin(self):
        rating_payload = {"data": [
            manga(1, "B", rating=2, author=" ", status="ongoing"),
            manga(2, "A", rating=5, artist="Leo", status="completed"),
            manga(3, "Novela", "web novel", rating=9),
        ], "totalPages": 4}
        az_payload = {"data": [manga(4, "Z"), manga(5, "C")], "totalPages": 2}
        fetcher = Fetcher([
            Response("https://manhuako.net/api/mangas", rating_payload),
            Response("https://manhuako.net/api/mangas", az_payload),
        ])
        source = source_class()(fetcher)

        first = await source.search("dragon", 2, {"sort": "rating", "genre": "accion"})
        second = await source.search("", 1, {"sort": "az", "genre": "accion"})

        self.assertEqual([item.id for item in source.get_filters()], ["sort", "genre"])
        self.assertEqual(source.get_filters()[0].default, "newest")
        self.assertEqual(source.get_filters()[1].default, "")
        self.assertEqual(fetcher.requests[0][2]["params"], {
            "page": "2", "limit": "20", "sort": "rating", "search": "dragon",
        })
        self.assertEqual(fetcher.requests[1][2]["params"]["genre"], "accion")
        self.assertEqual([item.title for item in first["items"]], ["A", "B"])
        self.assertTrue(first["has_more"])
        item = first["items"][0]
        self.assertEqual(item.source_id, "2|a")
        self.assertEqual(item.web_url, "https://manhuako.net/manga/a")
        self.assertEqual((item.author, item.artist, item.status), (None, "Leo", "completed"))
        self.assertEqual(item.content_tags, ("Manga",))
        self.assertEqual([item.title for item in second["items"]], ["C", "Z"])
        self.assertEqual(source.capabilities.headers["Accept"], "application/json, text/plain, */*")
        self.assertEqual(source.capabilities.content_warning, "mixed")

    async def test_popular_latest_and_client_side_sorting_match_api(self):
        popular = {"data": [manga(1, "B", views=3), manga(2, "A", views=8)], "totalPages": 2}
        latest = {"data": [
            manga(3, "Viejo", updated_at="2026-01-01T00:00:00.000Z"),
            manga(4, "Nuevo", updated_at="2026-08-04T00:00:00.000Z"),
        ], "totalPages": 1}
        fetcher = Fetcher([
            Response("https://manhuako.net/api/mangas", popular),
            Response("https://manhuako.net/api/mangas", latest),
        ])
        source = source_class()(fetcher)

        popular_result = await source.browse("popular", 1)
        latest_result = await source.browse("latest", 1)

        self.assertEqual(fetcher.requests[0][2]["params"]["sort"], "popular")
        self.assertEqual(fetcher.requests[1][2]["params"]["sort"], "newest")
        self.assertEqual([item.title for item in popular_result["items"]], ["A", "B"])
        self.assertEqual([item.title for item in latest_result["items"]], ["Nuevo", "Viejo"])
        self.assertTrue(popular_result["has_more"])
        self.assertFalse(latest_result["has_more"])

    async def test_all_chapter_pages_and_images_match_kotlin_endpoints(self):
        fetcher = Fetcher([
            Response("https://manhuako.net/api/chapters/paginated", {
                "chapters": [
                    {"id": 10, "number": 7.0, "title": "Inicio", "created_at": "2026-08-04T12:30:00.000Z"},
                    {"id": 11, "number": None, "title": "null", "created_at": None},
                ], "totalPages": 2,
            }),
            Response("https://manhuako.net/api/chapters/paginated", {
                "chapters": [{"id": 9, "number": 6.5, "title": "", "created_at": "mal"}], "totalPages": 2,
            }),
            Response("https://manhuako.net/api/chapter-pages", [
                {"image_url": "https://cdn.example/1.webp"}, {"image_url": "https://cdn.example/2.webp"},
            ]),
        ])
        source = source_class()(fetcher)

        chapters = await source.chapters("42|dragon")
        pages = await source.pages(chapters[0])

        self.assertEqual([chapter.title for chapter in chapters], ["Capítulo 7 - Inicio", "Capítulo", "Capítulo 6.5"])
        self.assertEqual(chapters[0].uploaded_at, "2026-08-04T12:30:00+00:00")
        self.assertIsNone(chapters[1].uploaded_at)
        self.assertEqual(fetcher.requests[0][2]["params"]["manga_id"], "42")
        self.assertEqual(fetcher.requests[1][2]["params"]["page"], "2")
        self.assertEqual(fetcher.requests[2][2]["params"], {"chapter_id": "10"})
        self.assertEqual([page.source_id for page in pages], ["https://cdn.example/1.webp", "https://cdn.example/2.webp"])


if __name__ == "__main__":
    unittest.main()
