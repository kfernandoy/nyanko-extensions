from __future__ import annotations

import unittest

from engines.manhwaz import ManhwaZSource


class Response:
    def __init__(self, text="", *, url="https://manhwa.test/", content=b""):
        self.text = text
        self.url = url
        self.content = content
        self.headers = {"Content-Type": "image/jpeg"}

    def raise_for_status(self):
        return None


class Fetcher:
    async def request(self, _method, url, **_kwargs):
        if url == "https://manhwa.test":
            return Response(
                '<div id="slide-top"><div class="item"><div class="info-item">'
                '<a href="/series/one">One</a></div></div></div>',
                url=url,
            )
        if url.endswith("/series/one"):
            return Response(
                '<li class="wp-manga-chapter"><a href="/chapter/3">Chapter 3</a></li>',
                url=url,
            )
        if url.endswith("/chapter/3"):
            return Response(
                '<div class="page-break"><img data-src="/images/1.jpg"></div>',
                url=url,
            )
        if url.endswith("/images/1.jpg"):
            return Response(url=url, content=b"jpeg")
        raise AssertionError(url)


class DemoManhwaZ(ManhwaZSource):
    name = "manhwa_en"
    display_name = "Manhwa"
    base_url = "https://manhwa.test"
    language = "en"


class ManhwaZTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = DemoManhwaZ(Fetcher())
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        pages = await source.pages(chapter)
        self.assertEqual(pages[0].source_id, "https://manhwa.test/images/1.jpg")
        self.assertEqual(b"".join((await source.page_bytes(pages[0])).chunks), b"jpeg")


if __name__ == "__main__":
    unittest.main()
