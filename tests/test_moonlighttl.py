from __future__ import annotations

import unittest

from engines.moonlighttl import MoonlightTLSource


class Response:
    def __init__(self, text="", *, url="https://moon.test/", data=None, content=b""):
        self.text = text
        self.url = url
        self._data = data or {}
        self.content = content
        self.headers = {"Content-Type": "image/jpeg"}

    def json(self):
        return self._data

    def raise_for_status(self):
        return None


class Fetcher:
    async def request(self, method, url, **kwargs):
        if url.endswith("/api/topSerie"):
            return Response(url=url, data={"response": {"diario": [[{"project": {"name": "One", "slug": "one"}}]]}})
        if url.endswith("/api/showProject/one"):
            return Response(url=url, data={"response": {"lastChapters": [{"num": 2, "slug": "two", "created_at": "2026-01-01"}]}})
        if url.endswith("/ver/one/two") and method == "GET":
            return Response('<form method="post" action="/unlock"><input name="x" value="1"></form>', url=url)
        if url.endswith("/unlock"):
            self.data = kwargs["data"]
            return Response('<main class="contenedor"><img data-src="/1.jpg"></main>', url=url)
        if url.endswith("/1.jpg"):
            return Response(url=url, content=b"jpeg")
        raise AssertionError(url)


class DemoMoonlight(MoonlightTLSource):
    name = "moon_es"
    display_name = "Moon"
    base_url = "https://moon.test"
    language = "es"


class MoonlightTLTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow_and_unlock(self):
        fetcher = Fetcher()
        source = DemoMoonlight(fetcher)
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        page = (await source.pages(chapter))[0]
        self.assertEqual(fetcher.data, {"x": "1"})
        self.assertEqual(b"".join((await source.page_bytes(page)).chunks), b"jpeg")


if __name__ == "__main__":
    unittest.main()
