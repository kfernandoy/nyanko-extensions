import unittest

from engines.libgroup import LibGroupSource


class Response:
    def __init__(self, data=None, content=b""):
        self.data, self.content = data or {}, content
        self.headers = {"Content-Type": "image/webp"}

    def json(self):
        return self.data

    def raise_for_status(self):
        pass


class Fetcher:
    async def request(self, _method, url, **_kwargs):
        if url.endswith("/api/manga"):
            return Response({"data": [{"slug_url": "1--one", "name": "One"}]})
        if url.endswith("/chapters"):
            return Response({"data": [{"volume": "1", "number": "2", "name": None, "branches": [{"branch_id": 3, "created_at": "2026-01-01", "restricted_view": {"is_open": True}}]}]})
        if "/chapter?" in url:
            return Response({"data": {"pages": [{"slug": 1, "url": "/one.webp"}]}})
        if url.endswith("/api/constants"):
            return Response({"data": {"imageServers": [{"url": "https://cdn.test", "site_ids": [1]}]}})
        if url == "https://cdn.test/one.webp":
            return Response(content=b"webp")
        raise AssertionError(url)


class Demo(LibGroupSource):
    name, display_name = "lib_ru", "Lib"
    base_url, language = "https://lib.test", "ru"


class LibGroupTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = Demo(Fetcher())
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        page = (await source.pages(chapter))[0]
        self.assertEqual(b"".join((await source.page_bytes(page)).chunks), b"webp")


if __name__ == "__main__":
    unittest.main()
