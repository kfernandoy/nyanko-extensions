import unittest

from engines.mangahub import MangaHubSource


class Response:
    def __init__(self, data=None, content=b""):
        self.data, self.content = data or {}, content
        self.headers = {"Content-Type": "image/jpeg"}

    def json(self):
        return self.data

    def raise_for_status(self):
        pass


class Fetcher:
    async def request(self, _method, url, **kwargs):
        if url.endswith("/graphql"):
            query = kwargs["json"]["query"]
            if "search(" in query:
                return Response({"data": {"search": {"rows": [{"slug": "one", "title": "One", "author": "A", "latestChapter": 2, "genres": "G"}]}}})
            if "chapters{" in query:
                return Response({"data": {"manga": {"slug": "one", "chapters": [{"number": 2, "title": "", "date": "2026-01-01"}]}}})
            return Response({"data": {"chapter": {"pages": '{"p":"one/","i":["1.jpg"]}'}}})
        if url == "https://imgx.mghcdn.com/one/1.jpg":
            return Response(content=b"jpg")
        raise AssertionError(url)


class Demo(MangaHubSource):
    name, display_name = "hub_en", "Hub"
    base_url, language, manga_source, access_key = "https://hub.test", "en", "m01", "key"


class MangaHubTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = Demo(Fetcher())
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        page = (await source.pages(chapter))[0]
        self.assertEqual(b"".join((await source.page_bytes(page)).chunks), b"jpg")


if __name__ == "__main__":
    unittest.main()
