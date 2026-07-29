from __future__ import annotations

import unittest

from engines.bakkin import BakkinSource


class Response:
    def __init__(self, payload=None, *, url="https://bak.test/", content=b""):
        self._payload = payload
        self.url = url
        self.content = content
        self.headers = {"Content-Type": "image/jpeg"}

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class Fetcher:
    async def request(self, _method, url, **_kwargs):
        if url.endswith("/main.php"):
            return Response(
                {
                    "one": {
                        "dir": "one",
                        "name": "One",
                        "volumes": [
                            {
                                "dir": "v1",
                                "name": "Volume 1",
                                "chapters": [
                                    {"dir": "c2", "name": "Chapter 2", "pages": ["p/1.jpg"]}
                                ],
                            }
                        ],
                    }
                },
                url=url,
            )
        if url.endswith("/p/1.jpg"):
            return Response(url=url, content=b"jpeg")
        raise AssertionError(url)


class DemoBakkin(BakkinSource):
    name = "bak_en"
    display_name = "Bakkin"
    base_url = "https://bak.test/"
    language = "en"


class BakkinTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = DemoBakkin(Fetcher())
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        pages = await source.pages(chapter)
        self.assertEqual(b"".join((await source.page_bytes(pages[0])).chunks), b"jpeg")


if __name__ == "__main__":
    unittest.main()
