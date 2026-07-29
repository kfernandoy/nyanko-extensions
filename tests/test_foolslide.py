from __future__ import annotations

import unittest

from engines.foolslide import FoolSlideSource


class Response:
    def __init__(self, text="", *, url="https://fool.test/", content=b""):
        self.text = text
        self.url = url
        self.content = content
        self.headers = {"Content-Type": "image/jpeg"}

    def raise_for_status(self):
        return None


class Fetcher:
    async def request(self, _method, url, **_kwargs):
        if "/directory/" in url:
            return Response(
                '<div class="group"><a href="/series/one/" title="One">One</a></div>',
                url=url,
            )
        if url.endswith("/series/one/"):
            return Response(
                '<div class="group"><div class="element">'
                '<a href="/chapter/2/" title="Two">Chapter 2</a></div></div>',
                url=url,
            )
        if url.endswith("/chapter/2/"):
            return Response('var pages = [{"url":"/pages/1.jpg"}];', url=url)
        if url.endswith("/pages/1.jpg"):
            return Response(url=url, content=b"jpeg")
        raise AssertionError(url)


class DemoFoolSlide(FoolSlideSource):
    name = "fool_en"
    display_name = "Fool"
    base_url = "https://fool.test"
    language = "en"


class FoolSlideTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = DemoFoolSlide(Fetcher())
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        pages = await source.pages(chapter)
        self.assertEqual(pages[0].source_id, "https://fool.test/pages/1.jpg")
        self.assertEqual(b"".join((await source.page_bytes(pages[0])).chunks), b"jpeg")


if __name__ == "__main__":
    unittest.main()
