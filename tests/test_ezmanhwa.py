from __future__ import annotations

import unittest

from engines.ezmanhwa import EZManhwaSource


class Response:
    def __init__(self, *, url="https://api.ez.test/", data=None, content=b""):
        self.url = url
        self._data = data or {}
        self.content = content
        self.headers = {"Content-Type": "image/jpeg"}

    def json(self):
        return self._data

    def raise_for_status(self):
        return None


class Fetcher:
    async def request(self, _method, url, **_kwargs):
        if url.endswith("/series"):
            return Response(url=url, data={"data": [{"slug": "one", "title": "One", "type": "MANHWA"}]})
        if url.endswith("/series/one/chapters"):
            return Response(url=url, data={"data": [{"slug": "two", "number": 2, "requiresPurchase": False}], "totalPages": 1})
        if url.endswith("/series/one/chapters/two"):
            return Response(url=url, data={"images": [{"url": "https://cdn.test/1.jpg"}]})
        if url == "https://cdn.test/1.jpg":
            return Response(url=url, content=b"jpeg")
        raise AssertionError(url)


class DemoEZ(EZManhwaSource):
    name = "ez_en"
    display_name = "EZ"
    base_url = "https://ez.test"
    api_url = "https://api.ez.test/api/v1"
    language = "en"


class EZManhwaTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = DemoEZ(Fetcher())
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        page = (await source.pages(chapter))[0]
        self.assertEqual(b"".join((await source.page_bytes(page)).chunks), b"jpeg")


if __name__ == "__main__":
    unittest.main()
