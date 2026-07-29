from __future__ import annotations

import unittest

from engines.senkuro import SenkuroSource


class Response:
    def __init__(self, *, url="https://api.senkuro.test/graphql", data=None, content=b""):
        self.url = url
        self._data = data or {}
        self.content = content
        self.headers = {"Content-Type": "image/jpeg"}

    def json(self):
        return self._data

    def raise_for_status(self):
        return None


class Fetcher:
    async def request(self, _method, url, **kwargs):
        operation = kwargs.get("json", {}).get("operationName")
        if operation == "searchTachiyomiManga":
            return Response(url=url, data={"data": {"mangaTachiyomiSearch": {"mangas": [{"id": "1", "slug": "one", "titles": [{"lang": "RU", "content": "One"}]}]}}})
        if operation == "fetchTachiyomiChapters":
            return Response(url=url, data={"data": {"mangaTachiyomiChapters": {"chapters": [{"id": "2", "slug": "two", "number": "2", "volume": "1", "teamIds": []}], "teams": []}}})
        if operation == "fetchTachiyomiChapterPages":
            return Response(url=url, data={"data": {"mangaTachiyomiChapterPages": {"pages": [{"url": "https://cdn.test/1.jpg"}]}}})
        if url == "https://cdn.test/1.jpg":
            return Response(url=url, content=b"jpeg")
        raise AssertionError(url)


class DemoSenkuro(SenkuroSource):
    name = "senkuro_ru"
    display_name = "Senkuro"
    base_url = "https://senkuro.test"
    language = "ru"


class SenkuroTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = DemoSenkuro(Fetcher())
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        page = (await source.pages(chapter))[0]
        self.assertEqual(b"".join((await source.page_bytes(page)).chunks), b"jpeg")


if __name__ == "__main__":
    unittest.main()
