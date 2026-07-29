import unittest

from engines.hentaihand import HentaiHandSource


class Response:
    def __init__(self, data=None, content=b""):
        self.data, self.content = data or {}, content
        self.headers = {"Content-Type": "image/jpeg"}

    def json(self):
        return self.data

    def raise_for_status(self):
        pass


class Fetcher:
    async def request(self, _method, url, **_kwargs):
        if url.endswith("/api/comics"):
            return Response({"data": [{"slug": "one", "title": "One"}]})
        if url.endswith("/api/comics/one"):
            return Response({"slug": "one", "updated_at": "2026-01-01"})
        if url.endswith("/api/comics/one/images"):
            return Response({"images": [{"page": 1, "source_url": "https://cdn.test/1.jpg"}]})
        if url == "https://cdn.test/1.jpg":
            return Response(content=b"jpg")
        raise AssertionError(url)


class Demo(HentaiHandSource):
    name, display_name = "hand_en", "Hand"
    base_url, language, language_ids = "https://hand.test", "en", [2]


class HentaiHandTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = Demo(Fetcher())
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        page = (await source.pages(chapter))[0]
        self.assertEqual(b"".join((await source.page_bytes(page)).chunks), b"jpg")


if __name__ == "__main__":
    unittest.main()
