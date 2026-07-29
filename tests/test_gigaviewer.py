from __future__ import annotations

import html
import unittest

from engines.gigaviewer import GigaViewerSource


class Response:
    def __init__(self, text="", payload=None, url="https://demo.test/"):
        self.text, self._payload, self.url = text, payload, url

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class Fetcher:
    async def request(self, method, url, **kwargs):
        if "pagination_readable_products" in url:
            return Response(payload=[] if kwargs["params"]["offset"] != "0" else [{"readable_product_id": "2", "title": "Episode 2"}])
        if url.endswith("/series/a"):
            return Response('<script class="js-valve" data-giga_series="a"></script>', url=url)
        if url.endswith("/episode/2"):
            payload = '{"readableProduct":{"pageStructure":{"choJuGiga":"","pages":[{"src":"https://cdn.test/1.jpg","type":"main"}]}}}'
            return Response(f'<script id="episode-json" data-value="{html.escape(payload, quote=True)}"></script>', url=url)
        return Response('<ul class="series-list"><li><a href="/series/a"><h2 class="series-list-title">Serie</h2></a></li></ul>', url=url)


class Demo(GigaViewerSource):
    name = "demo_ja"
    base_url = "https://demo.test"


class GigaViewerTest(unittest.IsolatedAsyncioTestCase):
    async def test_html_and_api_flow(self):
        source = Demo(Fetcher())
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        page = (await source.pages(chapter))[0]
        self.assertEqual((series.title, chapter.number, page.source_id), ("Serie", 2.0, "https://cdn.test/1.jpg"))


if __name__ == "__main__":
    unittest.main()
