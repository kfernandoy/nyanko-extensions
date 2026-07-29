import unittest

from engines.eromuse import EroMuseSource


class Response:
    def __init__(self, text="", content=b""):
        self.text, self.content = text, content
        self.headers = {"Content-Type": "image/jpeg"}

    def raise_for_status(self):
        pass


class Fetcher:
    async def request(self, _method, url, **_kwargs):
        if url.endswith("/comics/album/Various-Authors"):
            return Response('<a class="c-tile" href="/comics/album/One"><img src="/th/cover.jpg">One</a>')
        if url.endswith("/comics/album/One"):
            return Response('<a class="c-tile" href="/comics/picture/1"><img src="https://cdn.test/th/1.jpg">Page</a>')
        if url == "https://cdn.test/fl/1.jpg":
            return Response(content=b"jpg")
        raise AssertionError(url)


class Demo(EroMuseSource):
    name, display_name = "muse_en", "Muse"
    base_url, language = "https://muse.test", "en"


class EroMuseTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = Demo(Fetcher())
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        page = (await source.pages(chapter))[0]
        self.assertEqual(b"".join((await source.page_bytes(page)).chunks), b"jpg")


if __name__ == "__main__":
    unittest.main()
