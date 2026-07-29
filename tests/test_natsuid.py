from __future__ import annotations

import unittest

from engines.natsuid import NatsuIdSource


class Response:
    def __init__(self, text="", *, url="https://natsu.test/", content=b""):
        self.text = text
        self.url = url
        self.content = content
        self.headers = {"Content-Type": "image/jpeg"}

    def raise_for_status(self):
        return None


class Fetcher:
    async def request(self, method, url, **kwargs):
        params = kwargs.get("params", {})
        if params.get("action") == "get_nonce":
            return Response('<input name="search_nonce" value="abc">', url=url)
        if method == "POST":
            return Response(
                '<div><a href="/manga/one/"><img alt="One"></a></div>',
                url=url,
            )
        if url.endswith("/manga/one/"):
            return Response(
                '<div id="gallery-list" hx-get="/ajax?manga_id=7&amp;page=1"></div>',
                url=url,
            )
        if params.get("action") == "chapter_list":
            return Response(
                '<div><a href="/chapter/5"><span>Chapter 5</span>'
                '<time datetime="2025-01-01"></time></a></div>',
                url=url,
            )
        if url.endswith("/chapter/5"):
            return Response(
                '<main><div class="relative"><section>'
                '<img src="/images/1.jpg"></section></div></main>',
                url=url,
            )
        if url.endswith("/images/1.jpg"):
            return Response(url=url, content=b"jpeg")
        raise AssertionError((method, url, kwargs))


class DemoNatsuId(NatsuIdSource):
    name = "natsu_id"
    display_name = "Natsu"
    base_url = "https://natsu.test"
    language = "id"


class NatsuIdTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = DemoNatsuId(Fetcher())
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        pages = await source.pages(chapter)
        self.assertEqual(pages[0].source_id, "https://natsu.test/images/1.jpg")
        self.assertEqual(b"".join((await source.page_bytes(pages[0])).chunks), b"jpeg")


if __name__ == "__main__":
    unittest.main()
