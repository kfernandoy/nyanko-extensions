import unittest

from engines.galleryadults import GalleryAdultsSource


class Response:
    def __init__(self, text="", content=b""):
        self.text, self.content = text, content
        self.headers = {"Content-Type": "image/jpeg"}

    def raise_for_status(self):
        pass


class Fetcher:
    async def request(self, _method, url, **_kwargs):
        if url.endswith("/language/english/popular"):
            return Response('<div class="thumb"><div class="inner_thumb"><a href="/g/1"><img src="/1t.jpg"></a></div><div class="caption">One</div></div>')
        if url.endswith("/g/1"):
            return Response('<div class="gallery_thumb"><img src="https://cdn.test/1t.jpg"></div>')
        if url == "https://cdn.test/1.jpg":
            return Response(content=b"jpg")
        raise AssertionError(url)


class Demo(GalleryAdultsSource):
    name, display_name = "gallery_en", "Gallery"
    base_url, language, manga_language = "https://gallery.test", "en", "english"


class GalleryAdultsTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = Demo(Fetcher())
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        page = (await source.pages(chapter))[0]
        self.assertEqual(b"".join((await source.page_bytes(page)).chunks), b"jpg")


if __name__ == "__main__":
    unittest.main()
