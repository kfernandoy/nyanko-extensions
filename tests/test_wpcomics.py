from __future__ import annotations

import unittest

from engines.wpcomics import WPComicsSource


class Response:
    def __init__(self, text, url="https://demo.test/"):
        self.text, self.url = text, url

    def raise_for_status(self):
        pass


class Fetcher:
    async def request(self, method, url, **kwargs):
        if "chapter-1" in url:
            return Response('<div class="page-chapter"><img data-src="/1.jpg"></div>', url)
        if "serie" in url:
            return Response('<div class="list-chapter"><li class="row"><a href="/chapter-1">Chapter 1</a></li></div>', url)
        return Response('<div class="items"><div class="item"><h3><a href="/serie">Serie</a></h3></div></div>', url)


class Demo(WPComicsSource):
    name = "demo_en"
    base_url = "https://demo.test"


class WPComicsTest(unittest.IsolatedAsyncioTestCase):
    async def test_html_flow(self):
        source = Demo(Fetcher())
        series = (await source.search("serie"))[0]
        chapter = (await source.chapters(series))[0]
        page = (await source.pages(chapter))[0]
        self.assertEqual((series.title, chapter.number, page.source_id), ("Serie", 1.0, "https://demo.test/1.jpg"))


if __name__ == "__main__":
    unittest.main()
