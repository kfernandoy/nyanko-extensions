from __future__ import annotations

import unittest

from engines.manga18 import Manga18Source


class Response:
    def __init__(self, text="", *, url="https://adult.test/", content=b""):
        self.text = text
        self.url = url
        self.content = content
        self.headers = {"Content-Type": "image/jpeg"}

    def raise_for_status(self):
        return None


class Fetcher:
    async def request(self, _method, url, **_kwargs):
        if "/list-manga/" in url:
            return Response(
                '<div class="story_item"><div class="mg_name">'
                '<a href="/manga/one">One</a></div></div>',
                url=url,
            )
        if url.endswith("/manga/one"):
            return Response(
                '<div class="chapter_box"><div class="item">'
                '<a href="/chapter/2">Chapter 2</a></div></div>',
                url=url,
            )
        if url.endswith("/chapter/2"):
            return Response(
                '<script>const slides_p_path = ["L2ltYWdlcy8xLmpwZw==",];</script>',
                url=url,
            )
        if url.endswith("/images/1.jpg"):
            return Response(url=url, content=b"jpeg")
        raise AssertionError(url)


class DemoManga18(Manga18Source):
    name = "adult_en"
    display_name = "Adult"
    base_url = "https://adult.test"
    language = "en"


class Manga18Test(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = DemoManga18(Fetcher())
        series = (await source.browse("latest"))[0]
        chapter = (await source.chapters(series))[0]
        pages = await source.pages(chapter)
        self.assertEqual(pages[0].source_id, "https://adult.test/images/1.jpg")
        self.assertEqual(b"".join((await source.page_bytes(pages[0])).chunks), b"jpeg")


if __name__ == "__main__":
    unittest.main()
