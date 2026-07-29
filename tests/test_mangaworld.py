from __future__ import annotations

import unittest

from engines.mangaworld import MangaWorldSource


class Response:
    def __init__(self, text="", *, url="https://world.test/", content=b""):
        self.text = text
        self.url = url
        self.content = content
        self.headers = {"Content-Type": "image/jpeg"}

    def raise_for_status(self):
        return None


class Fetcher:
    async def request(self, _method, url, **kwargs):
        if url.endswith("/archive") and "Cookie" not in kwargs.get("headers", {}):
            return Response('<script>document.cookie="MWCookie=ok; path=/";</script>', url=url)
        if url.endswith("/archive"):
            return Response(
                '<div class="comics-grid"><div class="entry">'
                '<a href="/series/one" title="One"></a></div></div>',
                url=url,
            )
        if url.endswith("/series/one"):
            return Response(
                '<div class="chapters-wrapper"><div class="chapter">'
                '<a class="chap" href="/chapter/2"></a>'
                '<span class="d-inline-block">Capitolo 2</span></div></div>',
                url=url,
            )
        if "/chapter/2?style=list" in url:
            return Response(
                '<div id="page"><img class="page-image" src="/images/1.jpg"></div>',
                url=url,
            )
        if url.endswith("/images/1.jpg"):
            return Response(url=url, content=b"jpeg")
        raise AssertionError(url)


class DemoMangaWorld(MangaWorldSource):
    name = "world_it"
    display_name = "World"
    base_url = "https://world.test"
    language = "it"


class MangaWorldTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow_and_cookie_challenge(self):
        source = DemoMangaWorld(Fetcher())
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        pages = await source.pages(chapter)
        self.assertEqual(b"".join((await source.page_bytes(pages[0])).chunks), b"jpeg")


if __name__ == "__main__":
    unittest.main()
