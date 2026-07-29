from __future__ import annotations

import unittest

from engines.pizzareader import PizzaReaderSource


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
        if url.endswith("/api/comics"):
            return Response({"comics": [{"title": "Serie", "url": "/comic/serie"}]})
        if url.endswith("/api/comic/serie"):
            return Response(
                {
                    "comic": {
                        "chapters": [
                            {
                                "full_title": "Capítulo 4.2",
                                "chapter": 4,
                                "subchapter": 2,
                                "url": "/chapter/42",
                                "teams": [{"name": "Equipo"}],
                            }
                        ]
                    }
                }
            )
        if url.endswith("/api/chapter/42"):
            return Response({"chapter": {"pages": ["/pages/1.jpg"]}})
        if url.endswith("/pages/1.jpg"):
            return Response({}, content=b"jpeg")
        raise AssertionError(url)


class DemoPizzaReader(PizzaReaderSource):
    name = "pizza_it"
    display_name = "Pizza"
    base_url = "https://pizza.test"
    language = "it"


class PizzaReaderTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = DemoPizzaReader(Fetcher())
        series = (await source.browse("popular"))[0]
        chapters = await source.chapters(series)
        self.assertEqual((chapters[0].number, chapters[0].scanlator), (4.2, "Equipo"))
        pages = await source.pages(chapters[0])
        self.assertEqual(pages[0].source_id, "https://pizza.test/pages/1.jpg")
        self.assertEqual(b"".join((await source.page_bytes(pages[0])).chunks), b"jpeg")


if __name__ == "__main__":
    unittest.main()
