from __future__ import annotations

import unittest

from engines.mangabox import MangaBoxSource, _search_slug


class Response:
    def __init__(self, text="", *, url="https://box.test/", data=None, content=b""):
        self.text = text
        self.url = url
        self._data = data or {}
        self.content = content
        self.headers = {"Content-Type": "image/jpeg"}

    def json(self):
        return self._data

    def raise_for_status(self):
        return None


class Fetcher:
    async def request(self, _method, url, **_kwargs):
        if "/manga-list/" in url:
            return Response('<div class="list-truyen-item-wrap"><h3><a href="/manga/one">One</a></h3></div>', url=url)
        if url.endswith("/api/manga/one/chapters"):
            return Response(url=url, data={"data": {"chapters": [{"chapter_name": "Chapter 1", "chapter_slug": "chapter-1", "chapter_num": 1}]}})
        if url.endswith("/manga/one/chapter-1"):
            return Response('var cdns = ["https://cdn.test"]; var chapterImages = ["a/1.jpg"];', url=url)
        if url == "https://cdn.test/a/1.jpg":
            return Response(url=url, content=b"jpeg")
        raise AssertionError(url)


class DemoBox(MangaBoxSource):
    name = "box_en"
    display_name = "Box"
    base_url = "https://box.test"
    language = "en"


class MangaBoxTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = DemoBox(Fetcher())
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        page = (await source.pages(chapter))[0]
        self.assertEqual(b"".join((await source.page_bytes(page)).chunks), b"jpeg")

    def test_search_normalization(self):
        self.assertEqual(_search_slug("Đế Á"), "de_a")


if __name__ == "__main__":
    unittest.main()
