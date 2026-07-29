from __future__ import annotations

import unittest

from engines.spicytheme import SpicyThemeSource


class Response:
    def __init__(self, *, url="https://api.spicy.test/", data=None, content=b""):
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
        if url.endswith("/filtrar"):
            return Response(url=url, data={"data": [{"slug": "one", "name": "One"}]})
        if url.endswith("/serie/one"):
            return Response(url=url, data={"serie": {"chapters": [{"slug": "two", "num": 2}]}})
        if url.endswith("/serie/one/two/"):
            return Response(url=url, data={"pageches": {"urlImg": '["https://cdn.test/1.jpg"]'}})
        if url == "https://cdn.test/1.jpg":
            return Response(url=url, content=b"jpeg")
        raise AssertionError(url)


class DemoSpicy(SpicyThemeSource):
    name = "spicy_es"
    display_name = "Spicy"
    base_url = "https://spicy.test"
    language = "es"


class SpicyThemeTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = DemoSpicy(Fetcher())
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        page = (await source.pages(chapter))[0]
        self.assertEqual(b"".join((await source.page_bytes(page)).chunks), b"jpeg")


if __name__ == "__main__":
    unittest.main()
