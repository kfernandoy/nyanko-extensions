from __future__ import annotations

import unittest

from engines.mangataro import MangaTaroSource


class Response:
    def __init__(self, *, url="https://taro.test/", data=None, content=b""):
        self.url = url
        self._data = data if data is not None else {}
        self.content = content
        self.headers = {"Content-Type": "image/jpeg"}

    def json(self):
        return self._data

    def raise_for_status(self):
        return None


class Fetcher:
    async def request(self, _method, url, **_kwargs):
        if url.endswith("/load"):
            return Response(url=url, data=[{"id": "1", "url": "https://taro.test/manga/one", "title": "One", "type": "Manga"}])
        if url.endswith("/manga-chapters"):
            return Response(url=url, data={"chapters": [{"url": "/read/one/chapter-7", "chapter": "1", "language": "en"}]})
        if url.endswith("/chapter-content"):
            return Response(url=url, data={"images": ["https://cdn.test/1.jpg"]})
        if url == "https://cdn.test/1.jpg":
            return Response(url=url, content=b"jpeg")
        raise AssertionError(url)


class DemoTaro(MangaTaroSource):
    name = "taro_en"
    display_name = "Taro"
    base_url = "https://taro.test"
    language = "en"


class MangaTaroTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = DemoTaro(Fetcher())
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        page = (await source.pages(chapter))[0]
        self.assertEqual(b"".join((await source.page_bytes(page)).chunks), b"jpeg")


if __name__ == "__main__":
    unittest.main()
