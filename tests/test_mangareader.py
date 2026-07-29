from __future__ import annotations

import unittest

from engines.mangareader import MangaReaderSource


class Response:
    def __init__(self, text="", *, url="https://reader.test/", content=b"", payload=None):
        self.text = text
        self.url = url
        self.content = content
        self._payload = payload
        self.headers = {"Content-Type": "image/jpeg"}

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class Fetcher:
    async def request(self, _method, url, **_kwargs):
        if url.endswith("/filter"):
            return Response(
                '<div class="manga_list-sbs"><a class="manga-poster" href="/manga/one">'
                '<img alt="One"></a></div>',
                url=url,
            )
        if url.endswith("/manga/one"):
            return Response(
                '<ul id="en-chapters"><li class="chapter-item" data-id="8">'
                '<a href="/chapter/8"><span class="name">Chapter 8</span></a></li></ul>',
                url=url,
            )
        if "/ajax/image/list/8" in url:
            return Response(
                url=url,
                payload={
                    "html": '<div class="container-reader-chapter">'
                    '<div><img data-src="/images/1.jpg"></div></div>'
                },
            )
        if url.endswith("/images/1.jpg"):
            return Response(url=url, content=b"jpeg")
        raise AssertionError(url)


class DemoMangaReader(MangaReaderSource):
    name = "reader_en"
    display_name = "Reader"
    base_url = "https://reader.test"
    language = "en"


class MangaReaderTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = DemoMangaReader(Fetcher())
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        pages = await source.pages(chapter)
        self.assertEqual(pages[0].source_id, "https://reader.test/images/1.jpg")
        self.assertEqual(b"".join((await source.page_bytes(pages[0])).chunks), b"jpeg")


if __name__ == "__main__":
    unittest.main()
