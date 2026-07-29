import unittest

from engines.zmanga import ZMangaSource


class Response:
    def __init__(self, text="", content=b""):
        self.text, self.content = text, content
        self.headers = {"Content-Type": "image/jpeg"}

    def raise_for_status(self):
        pass


class Fetcher:
    async def request(self, _method, url, **_kwargs):
        if "advanced-search" in url:
            return Response('<div class="flexbox2-item"><div class="flexbox2-content"><a href="/series/one"><div class="flexbox2-title"><span>One</span></div></a></div></div>')
        if url.endswith("/series/one"):
            return Response('<ul class="series-chapterlist"><div class="flexch-infoz"><a href="/chapter/two"><span>Two</span><span class="date">Now</span></a></div></ul>')
        if url.endswith("/chapter/two"):
            return Response('<div class="reader-area"><img data-lazy-src="https://cdn.test/1.jpg"></div>')
        if url == "https://cdn.test/1.jpg":
            return Response(content=b"jpg")
        raise AssertionError(url)


class Demo(ZMangaSource):
    name, display_name = "zmanga_id", "ZManga"
    base_url, language = "https://zmanga.test", "id"


class ZMangaTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = Demo(Fetcher())
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        page = (await source.pages(chapter))[0]
        self.assertEqual(b"".join((await source.page_bytes(page)).chunks), b"jpg")


if __name__ == "__main__":
    unittest.main()
