from __future__ import annotations

import unittest

from engines.multichan import MultiChanSource


class Response:
    def __init__(self, text="", *, url="https://chan.test/", content=b"", status=200):
        self.text = text
        self.url = url
        self.content = content
        self.status_code = status
        self.headers = {"Content-Type": "image/jpeg"}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class Fetcher:
    async def request(self, _method, url, **_kwargs):
        if url.endswith("/mostfavorites"):
            return Response(
                '<div class="content_row" title="One"><h2>'
                '<a href="/manga/one"></a></h2></div>',
                url=url,
            )
        if url.endswith("/manga/one"):
            return Response(
                '<table class="table_cha"><tr><td>'
                '<a href="/chapter/2">Глава 2</a></td></tr></table>',
                url=url,
            )
        if url.endswith("/chapter/2"):
            return Response('{"fullimg":["https://img.test/1.jpg",]}', url=url)
        if url == "https://img.test/1.jpg":
            return Response(url=url, content=b"jpeg")
        raise AssertionError(url)


class DemoMultiChan(MultiChanSource):
    name = "chan_ru"
    display_name = "Chan"
    base_url = "https://chan.test"
    language = "ru"


class MultiChanTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = DemoMultiChan(Fetcher())
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        pages = await source.pages(chapter)
        self.assertEqual(b"".join((await source.page_bytes(pages[0])).chunks), b"jpeg")


if __name__ == "__main__":
    unittest.main()
