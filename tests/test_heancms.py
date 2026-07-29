from __future__ import annotations

import unittest

from engines.heancms import HeanCmsSource


class Response:
    def __init__(self, *, url="https://api.hean.test/", data=None, content=b""):
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
        if url.endswith("/chapter/query"):
            return Response(url=url, data={"data": [{"id": 8, "chapter_slug": "two", "chapter_name": "Chapter 2", "price": 0}], "meta": {"current_page": 1, "last_page": 1}})
        if url.endswith("/query"):
            return Response(url=url, data={"data": [{"id": 7, "series_slug": "one", "title": "One"}]})
        if "/chapter/one/two#8" in url:
            return Response(url=url, data={"chapter": {"chapter_data": {"images": ["https://cdn.test/1.jpg"]}}})
        if url == "https://cdn.test/1.jpg":
            return Response(url=url, content=b"jpeg")
        raise AssertionError(url)


class DemoHean(HeanCmsSource):
    name = "hean_en"
    display_name = "Hean"
    base_url = "https://hean.test"
    language = "en"


class HeanCmsTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = DemoHean(Fetcher())
        series = (await source.search("one"))[0]
        chapter = (await source.chapters(series))[0]
        page = (await source.pages(chapter))[0]
        self.assertEqual(b"".join((await source.page_bytes(page)).chunks), b"jpeg")


if __name__ == "__main__":
    unittest.main()
