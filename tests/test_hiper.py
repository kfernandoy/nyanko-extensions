from __future__ import annotations

import unittest

from engines.hiper import HiperSource


class Response:
    def __init__(self, *, data=None, content=b""):
        self._data = data or []
        self.content = content
        self.headers = {"Content-Type": "image/webp"}

    def json(self):
        return self._data

    def raise_for_status(self):
        return None


class Fetcher:
    async def request(self, _method, url, **_kwargs):
        if url.endswith("/search.query"):
            return Response(data=[{"result": {"data": {"json": {"hits": [{"id": 1, "slug": "one", "title": "One"}]}}}}])
        if url.endswith("/auth.me,series.chapters"):
            return Response(data=[{}, {"result": {"data": {"json": [{"number": 2.0, "title": None, "createdAt": "2026-01-01"}]}}}])
        if url.endswith("/auth.me,series.bySlug,reader.chapterPages"):
            return Response(data=[{}, {"result": {"data": {"json": [{"pageOrder": 1, "webpUrl": "https://cdn.test/1.webp", "avifUrl": None}]}}}])
        if url == "https://cdn.test/1.webp":
            return Response(content=b"webp")
        raise AssertionError(url)


class DemoHiper(HiperSource):
    name = "hiper_en"
    display_name = "Hiper"
    base_url = "https://hiper.test"
    language = "en"


class HiperTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = DemoHiper(Fetcher())
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        page = (await source.pages(chapter))[0]
        self.assertEqual(b"".join((await source.page_bytes(page)).chunks), b"webp")


if __name__ == "__main__":
    unittest.main()
