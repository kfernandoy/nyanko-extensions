from __future__ import annotations

import json
import unittest

from engines.liliana import LilianaSource


class Response:
    def __init__(self, text="", *, url="https://lili.test/", content=b"", payload=None):
        self.text = text
        self.url = url
        self.content = content
        self._payload = payload
        self.headers = {"Content-Type": "image/jpeg"}

    def raise_for_status(self):
        return None

    def json(self):
        if self._payload is None:
            return json.loads(self.text)
        return self._payload


class Fetcher:
    async def request(self, _method, url, **_kwargs):
        if "/ranking/" in url:
            return Response(
                '<div id="main"><div class="grid"><div><div class="text-center">'
                '<a href="/manga/one">One</a></div></div></div></div>',
                url=url,
            )
        if url.endswith("/manga/one"):
            return Response(
                '<ul><li class="chapter"><a href="/chapter/6">Chapter 6</a></li></ul>',
                url=url,
            )
        if url.endswith("/chapter/6"):
            return Response("<script>const CHAPTER_ID = 42;</script>", url=url)
        if "/ajax/image/list/chap/42" in url:
            return Response(
                url=url,
                payload={
                    "status": True,
                    "html": '<div class="separator"><a href="/images/1.jpg"></a></div>',
                },
            )
        if url.endswith("/images/1.jpg"):
            return Response(url=url, content=b"jpeg")
        raise AssertionError(url)


class DemoLiliana(LilianaSource):
    name = "lili_en"
    display_name = "Liliana"
    base_url = "https://lili.test"
    language = "en"


class DokirawFetcher:
    async def request(self, _method, url, **_kwargs):
        if url.endswith("/hot"):
            return Response(
                '<div class="manga-item_item-1"><a href="/manga/raw">'
                "<h3>Raw</h3></a></div>",
                url=url,
            )
        if url.endswith("/manga/raw"):
            return Response(
                '<a href="/chapter/raw"><div class="manga-detail_chapter-1">'
                "<span>Chapter 7</span></div></a>",
                url=url,
            )
        if url.endswith("/chapter/raw"):
            return Response(
                '<div class="page-chapter"><img data-cdn="/images/raw.jpg"></div>',
                url=url,
            )
        if url.endswith("/images/raw.jpg"):
            return Response(url=url, content=b"raw")
        raise AssertionError(url)


class DemoDokiraw(LilianaSource):
    name = "doki_ja"
    display_name = "Dokiraw"
    base_url = "https://doki.test"
    language = "ja"
    profile = "dokiraw"


class LilianaTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = DemoLiliana(Fetcher())
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        pages = await source.pages(chapter)
        self.assertEqual(pages[0].source_id, "https://lili.test/images/1.jpg")
        self.assertEqual(b"".join((await source.page_bytes(pages[0])).chunks), b"jpeg")

    async def test_dokiraw_profile(self):
        source = DemoDokiraw(DokirawFetcher())
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        pages = await source.pages(chapter)
        self.assertEqual(pages[0].source_id, "https://doki.test/images/raw.jpg")
        self.assertEqual(b"".join((await source.page_bytes(pages[0])).chunks), b"raw")


if __name__ == "__main__":
    unittest.main()
