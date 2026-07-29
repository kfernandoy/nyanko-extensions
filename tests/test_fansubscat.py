from __future__ import annotations

import unittest

from engines.fansubscat import FansubsCatSource


class Response:
    def __init__(self, *, url="https://api.fans.test/", data=None, content=b""):
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
        if "/manga/popular/" in url:
            return Response(url=url, data={"result": [{"slug": "one", "name": "One"}]})
        if "/manga/chapters/one" in url:
            return Response(url=url, data={"result": [{"id": 7, "title": "Chapter 1", "number": 1}]})
        if "/manga/pages/7" in url:
            return Response(url=url, data={"result": [{"url": "https://cdn.test/1.jpg"}]})
        if url == "https://cdn.test/1.jpg":
            return Response(url=url, content=b"jpeg")
        raise AssertionError(url)


class DemoFans(FansubsCatSource):
    name = "fans_ca"
    display_name = "Fans"
    base_url = "https://manga.fans.test"
    language = "ca"


class FansubsCatTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = DemoFans(Fetcher())
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        page = (await source.pages(chapter))[0]
        self.assertEqual(b"".join((await source.page_bytes(page)).chunks), b"jpeg")


if __name__ == "__main__":
    unittest.main()
