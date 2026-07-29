from __future__ import annotations

import unittest

from engines.monochrome import MonochromeSource


class Response:
    def __init__(self, payload=None, *, url="https://api.test/", content=b""):
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
        if url.endswith("/manga"):
            return Response({"results": [{"id": "m1", "title": "One"}]}, url=url)
        if url.endswith("/manga/m1/chapters"):
            return Response(
                [{"id": "c1", "version": 2, "length": 1, "number": 3, "name": "Three"}],
                url=url,
            )
        if "/media/c1/1.jpg" in url:
            return Response(url=url, content=b"jpeg")
        raise AssertionError(url)


class DemoMonochrome(MonochromeSource):
    name = "mono_en"
    display_name = "Mono"
    base_url = "https://mono.test"
    api_url = "https://api.test"
    language = "en"


class MonochromeTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = DemoMonochrome(Fetcher())
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        pages = await source.pages(chapter)
        self.assertEqual(b"".join((await source.page_bytes(pages[0])).chunks), b"jpeg")


if __name__ == "__main__":
    unittest.main()
