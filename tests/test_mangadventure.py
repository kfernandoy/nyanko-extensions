from __future__ import annotations

import unittest

from engines.mangadventure import MangAdventureSource


class Response:
    def __init__(self, *, url="https://ma.test/", data=None, content=b""):
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
            return Response(url=url, data={"results": [{"slug": "one", "title": "One"}], "last": True})
        if url.endswith("/series/one/chapters"):
            return Response(url=url, data={"results": [{"id": 7, "full_title": "Chapter 1", "number": 1, "groups": []}]})
        if url.endswith("/chapters/7/pages"):
            return Response(url=url, data={"results": [{"number": 1, "image": "https://cdn.test/1.jpg"}]})
        if url == "https://cdn.test/1.jpg":
            return Response(url=url, content=b"jpeg")
        raise AssertionError(url)


class DemoMA(MangAdventureSource):
    name = "ma_en"
    display_name = "MA"
    base_url = "https://ma.test"
    language = "en"


class MangAdventureTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = DemoMA(Fetcher())
        series = (await source.browse("latest"))[0]
        chapter = (await source.chapters(series))[0]
        page = (await source.pages(chapter))[0]
        self.assertEqual(b"".join((await source.page_bytes(page)).chunks), b"jpeg")


if __name__ == "__main__":
    unittest.main()
