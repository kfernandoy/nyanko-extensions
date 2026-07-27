from __future__ import annotations

import unittest

from bundles.mangadex_es import MangaDexEsSource


class Response:
    def __init__(self, payload=None, *, content=b"", content_type="application/json"):
        self._payload = payload or {}
        self.content = content
        self.headers = {"Content-Type": content_type}

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class Fetcher:
    def __init__(self):
        self.at_home_calls = 0

    async def request(self, _method, url, **kwargs):
        params = dict(kwargs.get("params") or [])
        if url.endswith("/manga"):
            return Response({"data": [{"id": "m1", "attributes": {"title": {"es": "Serie"}}}]})
        if url.endswith("/manga/m1/feed"):
            pages = 0 if params["includeEmptyPages"] == 1 else 2
            suffix = "empty" if pages == 0 else "readable"
            return Response(
                {
                    "data": [
                        {
                            "id": suffix,
                            "attributes": {
                                "chapter": "1.5",
                                "title": "Prueba",
                                "pages": pages,
                                "translatedLanguage": "es",
                                "publishAt": "2026-07-27T00:00:00Z",
                            },
                            "relationships": [],
                        }
                    ]
                }
            )
        if "/at-home/server/" in url:
            self.at_home_calls += 1
            return Response(
                {
                    "baseUrl": "https://uploads.example",
                    "chapter": {"hash": "hash", "data": ["001.jpg", "002.jpg"]},
                }
            )
        if url.startswith("https://uploads.example/"):
            return Response(content=b"jpeg", content_type="image/jpeg")
        raise AssertionError(url)


class MangaDexEsTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow_including_empty_and_fresh_page_resolution(self):
        fetcher = Fetcher()
        source = MangaDexEsSource(fetcher)

        self.assertEqual((await source.search("serie"))[0].title, "Serie")
        chapters = await source.chapters("m1")
        self.assertEqual({chapter.number for chapter in chapters}, {1.5})
        empty = next(chapter for chapter in chapters if chapter.source_id.endswith("|empty"))
        readable = next(chapter for chapter in chapters if not chapter.source_id.endswith("|empty"))
        self.assertEqual(await source.pages(empty), [])

        pages = await source.pages(readable)
        content = await source.page_bytes(pages[0])
        self.assertEqual(b"".join(content.chunks), b"jpeg")
        self.assertEqual(fetcher.at_home_calls, 2)


if __name__ == "__main__":
    unittest.main()
