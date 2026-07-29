from __future__ import annotations

import unittest

from engines.fuzzydoodle import FuzzyDoodleSource


class Response:
    def __init__(self, text="", *, url="https://fuzzy.test/", content=b""):
        self.text = text
        self.url = url
        self.content = content
        self.headers = {"Content-Type": "image/jpeg"}

    def raise_for_status(self):
        return None


class Fetcher:
    async def request(self, _method, url, **_kwargs):
        if "/manga?" in url:
            return Response('<div id="card-real"><a href="/one"><h2 class="text-sm">One</h2></a></div>', url=url)
        if url.endswith("/one"):
            return Response('<div id="chapters-list"><a href="/chapter/1"><span>Chapter 1</span></a></div>', url=url)
        if url.endswith("/chapter/1"):
            return Response('<div id="chapter-container"><img data-src="/1.jpg"></div>', url=url)
        if url.endswith("/1.jpg"):
            return Response(url=url, content=b"jpeg")
        raise AssertionError(url)


class DemoFuzzy(FuzzyDoodleSource):
    name = "fuzzy_en"
    display_name = "Fuzzy"
    base_url = "https://fuzzy.test"
    language = "en"


class FuzzyDoodleTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = DemoFuzzy(Fetcher())
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        page = (await source.pages(chapter))[0]
        self.assertEqual(b"".join((await source.page_bytes(page)).chunks), b"jpeg")


if __name__ == "__main__":
    unittest.main()
