from __future__ import annotations

import unittest

from engines.colorlibanime import ColorlibAnimeSource


class Response:
    def __init__(self, text="", *, url="https://color.test/", content=b""):
        self.text = text
        self.url = url
        self.content = content
        self.headers = {"Content-Type": "image/jpeg"}

    def raise_for_status(self):
        return None


class Fetcher:
    async def request(self, _method, url, **_kwargs):
        if url.endswith("/manga"):
            return Response(
                '<div class="product__item"><a class="img-link" href="/series/one"></a>'
                "<h5>One</h5></div>",
                url=url,
            )
        if url.endswith("/series/one"):
            return Response(
                '<div class="anime__details__episodes">'
                '<a href="/chapter/1">Chapter 1</a></div>',
                url=url,
            )
        if url.endswith("/chapter/1"):
            return Response('<div class="read-img"><img src="/images/1.jpg"></div>', url=url)
        if url.endswith("/images/1.jpg"):
            return Response(url=url, content=b"jpeg")
        raise AssertionError(url)


class DemoColorlib(ColorlibAnimeSource):
    name = "color_id"
    display_name = "Color"
    base_url = "https://color.test"
    language = "id"


class ColorlibAnimeTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = DemoColorlib(Fetcher())
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        pages = await source.pages(chapter)
        self.assertEqual(b"".join((await source.page_bytes(pages[0])).chunks), b"jpeg")


if __name__ == "__main__":
    unittest.main()
