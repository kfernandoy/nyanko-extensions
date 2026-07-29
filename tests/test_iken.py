from __future__ import annotations

import unittest

from engines.iken import IkenSource


class Response:
    def __init__(self, payload, *, content=b""):
        self._payload = payload
        self.content = content
        self.headers = {"Content-Type": "image/webp"}

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class Fetcher:
    async def request(self, _method, url, **kwargs):
        if url.endswith("/api/query"):
            return Response({"posts": [{"id": 7, "slug": "serie", "postTitle": "Serie"}]})
        if url.endswith("/api/post"):
            return Response(
                {
                    "post": {
                        "chapters": [
                            {
                                "id": 8,
                                "slug": "chapter-2",
                                "number": 2,
                                "title": "Título",
                                "isAccessible": True,
                                "createdAt": "2026-01-01T00:00:00Z",
                            }
                        ]
                    }
                }
            )
        if url.endswith("/api/chapter"):
            return Response(
                {"chapter": {"images": [{"url": "https://cdn.test/2.webp", "order": 2}]}}
            )
        if url.endswith("/2.webp"):
            return Response({}, content=b"webp")
        raise AssertionError((url, kwargs))


class DemoIken(IkenSource):
    name = "iken_en"
    display_name = "Iken"
    base_url = "https://iken.test"
    language = "en"


class IkenTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = DemoIken(Fetcher())
        series = (await source.browse("popular"))[0]
        chapters = await source.chapters(series)
        self.assertEqual(chapters[0].title, "Chapter 2 - Título")
        pages = await source.pages(chapters[0])
        self.assertEqual(pages[0].source_id, "https://cdn.test/2.webp")
        self.assertEqual(b"".join((await source.page_bytes(pages[0])).chunks), b"webp")


if __name__ == "__main__":
    unittest.main()
