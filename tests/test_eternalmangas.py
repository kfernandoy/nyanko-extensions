from __future__ import annotations

import unittest
from pathlib import Path

from tools.generate import _iken_bundle, _supported_iken


class Response:
    def __init__(self, url, payload, status=200):
        self.url, self.payload, self.status_code = url, payload, status

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
    module = root.parent / "extensions-source-main" / "src" / "es" / "eternalmangas"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_iken(module, build)
    assert config is not None
    bundle = _iken_bundle(
        (root / "engines" / "base.py").read_text(encoding="utf-8"),
        (root / "engines" / "iken.py").read_text(encoding="utf-8"),
        config,
    )
    namespace = {"__name__": "test_eternalmangas_bundle"}
    exec(compile(bundle, "eternalmangas_es.py", "exec"), namespace)
    return namespace["SOURCE"]


def manga(identifier=7, slug="gato", **extra):
    return {
        "id": identifier, "slug": slug, "postTitle": "Gato", "postContent": "Uno<br>Dos",
        "featuredImage": "https://cdn/cover.jpg", "alternativeTitles": "Cat", "author": "Ana",
        "artist": "Leo", "seriesType": "MANHWA", "seriesStatus": "ONGOING",
        "genres": [{"id": 1, "name": "Accion"}], "chapters": [], **extra,
    }


class EternalMangasTest(unittest.IsolatedAsyncioTestCase):
    async def test_popular_and_latest_use_the_same_orders_as_kotlin(self):
        fetcher = Fetcher([
            Response("https://api.eternalmangas.org/api/query", {
                "posts": [manga()], "totalCount": 19,
            }),
            Response("https://api.eternalmangas.org/api/query", {
                "posts": [manga(8, "perro")], "totalCount": 18,
            }),
        ])
        source = source_class()(fetcher)

        popular = await source.browse("popular", 1)
        latest = await source.browse("latest", 1)

        self.assertEqual(fetcher.requests[0][2]["params"]["orderBy"], "totalViews")
        self.assertEqual(fetcher.requests[1][2]["params"]["orderBy"], "lastChapterAddedAt")
        self.assertTrue(popular["has_more"])
        self.assertFalse(latest["has_more"])

    async def test_dynamic_filters_skip_novel_page_and_map_full_manga(self):
        fetcher = Fetcher([
            Response("https://api.eternalmangas.org/api/genres", [{"id": 2, "name": "Drama"}]),
            Response("https://api.eternalmangas.org/api/query", {
                "posts": [manga(1, "novela", isNovel=True)], "totalCount": 40,
            }),
            Response("https://api.eternalmangas.org/api/query", {
                "posts": [manga()], "totalCount": 40,
            }),
        ])
        source = source_class()(fetcher)

        filters = await source.get_filters()
        result = await source.search(" gato ", 1, {
            "status": "ONGOING", "type": "MANHWA", "sort": "postTitle",
            "direction": "asc", "genres": ["2", "4"],
        })

        self.assertEqual([item.id for item in filters], ["status", "type", "sort", "direction", "genres"])
        self.assertEqual(fetcher.requests[1][2]["params"]["page"], "1")
        params = fetcher.requests[2][2]["params"]
        self.assertEqual(params["page"], "2")
        self.assertEqual(params["genreIds"], "2,4")
        self.assertEqual(params["searchTerm"], "gato")
        item = result["items"][0]
        self.assertEqual(item.source_id, "gato#7")
        self.assertEqual(item.description, "Uno Dos\n\nAlternative Names: Cat")
        self.assertEqual((item.author, item.artist, item.status), ("Ana", "Leo", "ongoing"))
        self.assertEqual(item.content_tags, ("Manhwa", "Accion"))
        self.assertEqual(item.web_url, "https://eternalmangas.org/series/gato")
        self.assertTrue(result["has_more"])
        self.assertEqual(source.capabilities.content_warning, "mixed")

    async def test_chapter_visibility_metadata_and_view_update_match_kotlin(self):
        post = manga(chapters=[
            {"id": 10, "slug": "c1", "number": 1, "title": "Inicio", "createdAt": "2026-08-04T10:00:00Z", "isAccessible": True, "createdBy": {"name": "Scan"}},
            {"id": 11, "slug": "c2", "number": 2, "title": None, "createdAt": "2026-08-04T11:00:00Z", "isAccessible": False, "isLocked": True},
            {"id": 12, "slug": "c3", "number": 3, "title": None, "createdAt": "2026-08-04T12:00:00Z", "isAccessible": False},
        ])
        fetcher = Fetcher([
            Response("https://api.eternalmangas.org/api/post", {"post": post}),
            Response("https://api.eternalmangas.org/api/analytics/updateViews", {}),
        ])
        source = source_class()(fetcher)

        chapters = await source.chapters("gato#7")

        self.assertEqual([chapter.title for chapter in chapters], ["Chapter 1 - Inicio"])
        self.assertEqual(chapters[0].source_id, "/series/gato/c1#10")
        self.assertEqual(chapters[0].scanlator, "Scan")
        self.assertEqual(chapters[0].uploaded_at, "2026-08-04T10:00:00+00:00")
        self.assertEqual(fetcher.requests[1][0:2], ("POST", "https://api.eternalmangas.org/api/analytics/updateViews"))
        self.assertEqual(fetcher.requests[1][2]["json"], {"postId": 7, "chapterId": None})
        self.assertFalse(source.get_preferences()[0].default)

    async def test_page_locks_sorting_encoding_and_analytics_match_kotlin(self):
        locked = source_class()(Fetcher([Response(
            "https://api.eternalmangas.org/api/chapter", {"chapter": {"isLockedByCoins": True}},
        )]))
        with self.assertRaisesRegex(ValueError, "monedas"):
            await locked.pages("/series/gato/c1#10")

        fetcher = Fetcher([
            Response("https://api.eternalmangas.org/api/chapter", {"chapter": {"images": [
                {"url": "https://cdn/page 2.jpg", "order": 2},
                {"url": "https://cdn/page 1.jpg", "order": 1},
                {"url": "https://cdn/final.jpg", "order": None},
            ]}}),
            Response("https://api.eternalmangas.org/api/analytics/updateViews", {}),
        ])
        source = source_class()(fetcher)

        pages = await source.pages("/series/gato/c1#10")

        self.assertEqual([page.source_id for page in pages], [
            "https://cdn/page%201.jpg", "https://cdn/page%202.jpg", "https://cdn/final.jpg",
        ])
        self.assertEqual(fetcher.requests[1][2]["json"], {"postId": None, "chapterId": "10"})


if __name__ == "__main__":
    unittest.main()
