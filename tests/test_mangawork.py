from __future__ import annotations

import unittest

from engines.mangawork import MangaWorkSource


class Response:
    def __init__(self, text="", *, url="https://work.test/", content=b""):
        self.text = text
        self.url = url
        self.content = content
        self.headers = {"Content-Type": "image/jpeg"}

    def raise_for_status(self):
        return None


class Fetcher:
    async def request(self, _method, url, **_kwargs):
        if "/series/" in url:
            return Response('<div class="w-full h-full"><a href="/manga/one"><h1>One</h1></a></div>', url=url)
        if url.endswith("/manga/one"):
            return Response('<div id="chapter_list" data-post-id="7"><li><a href="/chapter/1"><span class="m-0">Chapter 1</span></a></li></div>', url=url)
        if url.endswith("/chapter/1"):
            return Response('<div class="reader-area"><img id="imagech" data-src="/1.jpg"></div>', url=url)
        if url.endswith("/1.jpg"):
            return Response(url=url, content=b"jpeg")
        raise AssertionError(url)


class DemoWork(MangaWorkSource):
    name = "work_pt"
    display_name = "Work"
    base_url = "https://work.test"
    language = "pt-BR"


class MangaWorkTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = DemoWork(Fetcher())
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        page = (await source.pages(chapter))[0]
        self.assertEqual(b"".join((await source.page_bytes(page)).chunks), b"jpeg")


if __name__ == "__main__":
    unittest.main()
