from __future__ import annotations

import unittest

from engines.fmreader import FMReaderSource


class Response:
    def __init__(self, text="", *, url="https://fm.test/", content=b""):
        self.text = text
        self.url = url
        self.content = content
        self.headers = {"Content-Type": "image/jpeg"}

    def raise_for_status(self):
        return None


class Fetcher:
    async def request(self, _method, url, **_kwargs):
        if "/manga-list.html" in url:
            return Response('<div class="media"><h3><a href="/manga-one.html">One</a></h3></div>', url=url)
        if url.endswith("/manga-one.html"):
            return Response('<div class="list-chapters"><a href="/chapter-1"><span>Chapter 1</span></a></div>', url=url)
        if url.endswith("/chapter-1"):
            return Response('<img class="chapter-img" data-src="/1.jpg">', url=url)
        if url.endswith("/1.jpg"):
            return Response(url=url, content=b"jpeg")
        raise AssertionError(url)


class DemoFM(FMReaderSource):
    name = "fm_ja"
    display_name = "FM"
    base_url = "https://fm.test"
    language = "ja"


class FMReaderTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = DemoFM(Fetcher())
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        page = (await source.pages(chapter))[0]
        self.assertEqual(b"".join((await source.page_bytes(page)).chunks), b"jpeg")


if __name__ == "__main__":
    unittest.main()
