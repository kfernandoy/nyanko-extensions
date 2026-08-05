from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.generate import _extract_kotlin_metadata, _generic_bundle, _supported_generic


class Response:
    def __init__(self, url: str, payload, content: bytes = b""):
        self.url, self.status_code, self.content, self.headers = url, 200, content, {"Content-Type": "image/jpeg"}
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        stream = json.dumps([1, f'0:{{"payload":{data}}}\n'])
        self.text = f"<script>self.__next_f.push({stream})</script>"

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
    module = root.parent / "extensions-source-main" / "src" / "es" / "lectormonline"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_generic(module, build)
    assert config is not None
    config["content_warning"] = _extract_kotlin_metadata(module)
    bundle = _generic_bundle(
        (root / "engines" / "madara.py").read_text(encoding="utf-8"),
        (root / "engines" / "generic.py").read_text(encoding="utf-8"),
        config,
    )
    namespace = {"__name__": "test_lectormonline_bundle"}
    exec(compile(bundle, "lectormonline_es.py", "exec"), namespace)
    return namespace["SOURCE"]


def comic(path: str, name: str) -> dict:
    return {
        "name": name, "comic_path": path, "cover_image": f"https://cdn{path}.jpg",
        "state": "ONGOING", "genres": ["Acción"], "description": "Sinopsis",
    }


class MangoLibreriaTest(unittest.IsolatedAsyncioTestCase):
    async def test_paginated_catalogs_and_search(self):
        first = {"comicsData": {"comics": [comic("/comic/gato", "Gato")], "page": 1, "totalPages": 2}}
        last = {"comicsData": {"comics": [comic("/comic/perro", "Perro")], "page": 2, "totalPages": 2}}
        fetcher = Fetcher([
            Response("https://mangolibreria.com/comics?sort=views&page=1", first),
            Response("https://mangolibreria.com/comics?page=2", last),
            Response("https://mangolibreria.com/comics?page=1&q=gato", first),
        ])
        source = source_class()(fetcher)

        popular = await source.browse("popular", 1)
        latest = await source.browse("latest", 2)
        search = await source.search(" gato ", 1)

        self.assertEqual((popular["items"][0].title, popular["has_more"]), ("Gato", True))
        self.assertEqual((latest["items"][0].title, latest["has_more"]), ("Perro", False))
        self.assertEqual(search["items"][0].content_tags, ("Acción",))
        self.assertEqual([request[2]["params"] for request in fetcher.requests], [
            {"page": "1", "sort": "views"}, {"page": "2"}, {"page": "1", "q": "gato"},
        ])
        self.assertEqual(source.capabilities.content_warning, "mixed")

    async def test_details_chapters_pages_and_external_cdn_headers(self):
        details = {"comicData": {
            "title": "Gato", "description": "Sinopsis", "cover_image": "https://cdn/gato.jpg",
            "state": "COMPLETED", "genres": [{"name": "Acción"}],
            "scan_groups": [{"name": "Equipo", "chapters": [
                {"chapter_number": "1.0", "chapter_path": "/comic/gato/1", "created_at": "2026-08-03 10:11:12"},
                {"chapter_number": "2", "title": "Final", "chapter_path": "/comic/gato/2", "release_date": "2026-08-04T10:11:12.123Z"},
            ]}],
            "url_pages": ["https://cdn/pages/1.jpg"],
        }}
        fetcher = Fetcher([
            Response("https://mangolibreria.com/comic/gato", details),
            Response("https://mangolibreria.com/comic/gato", details),
            Response("https://mangolibreria.com/comic/gato/2", details),
            Response("https://cdn/pages/1.jpg", {}, b"image"),
        ])
        source = source_class()(fetcher)

        manga = await source.details("/comic/gato")
        chapters = await source.chapters(manga)
        pages = await source.pages(chapters[0])
        await source.page_bytes(pages[0])

        self.assertEqual((manga.status, manga.content_tags), ("completed", ("Acción",)))
        self.assertEqual((chapters[0].title, chapters[0].number, chapters[0].scanlator), ("Final", 2.0, "Equipo"))
        self.assertEqual(chapters[0].uploaded_at, "2026-08-04T10:11:12.123000+00:00")
        self.assertEqual(pages[0].source_id, "https://cdn/pages/1.jpg")
        self.assertEqual(fetcher.requests[-1][2]["headers"], {})


if __name__ == "__main__":
    unittest.main()
