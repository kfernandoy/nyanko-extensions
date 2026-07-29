from __future__ import annotations

import unittest

from engines.guya import GuyaSource


class Response:
    def __init__(self, payload, *, content=b""):
        self._payload = payload
        self.content = content
        self.headers = {"Content-Type": "image/jpeg"}

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class Fetcher:
    async def request(self, _method, url, **_kwargs):
        if url.endswith("/api/get_all_series/"):
            return Response({"Serie": {"slug": "serie", "last_updated": 2}})
        if url.endswith("/api/series/serie/"):
            return Response(
                {
                    "groups": {"1": "Equipo"},
                    "preferred_sort": ["1"],
                    "chapters": {
                        "3": {
                            "title": "Título",
                            "folder": "003",
                            "groups": {"1": ["001.jpg"]},
                        }
                    },
                }
            )
        if url.endswith("/001.jpg"):
            return Response({}, content=b"jpeg")
        raise AssertionError(url)


class DemoGuya(GuyaSource):
    name = "guya_en"
    display_name = "Guya"
    base_url = "https://guya.test"
    language = "en"


class GuyaTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = DemoGuya(Fetcher())
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        self.assertEqual((chapter.number, chapter.scanlator), (3, "Equipo"))
        pages = await source.pages(chapter)
        self.assertTrue(pages[0].source_id.endswith("/003/1/001.jpg"))
        self.assertEqual(b"".join((await source.page_bytes(pages[0])).chunks), b"jpeg")


if __name__ == "__main__":
    unittest.main()
