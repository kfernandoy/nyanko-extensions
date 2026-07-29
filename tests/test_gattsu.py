from __future__ import annotations

import unittest

from engines.gattsu import GattsuSource


class Response:
    def __init__(self, text="", *, url="https://gattsu.test/", content=b""):
        self.text = text
        self.url = url
        self.content = content
        self.headers = {"Content-Type": "image/jpeg"}

    def raise_for_status(self):
        return None


class Fetcher:
    async def request(self, _method, url, **_kwargs):
        if url.rstrip("/") == "https://gattsu.test":
            return Response(
                '<div class="lista"><ul><li><a href="https://gattsu.test/post">'
                '<span class="thumb-titulo">One</span></a></li></ul></div>',
                url=url,
            )
        if url.endswith("/post"):
            return Response(
                '<div class="post-box"><ul class="post-fotos"><li><a>'
                '<img data-src="/page-300x400.jpg"></a></li></ul></div>',
                url=url,
            )
        if url.endswith("/page.jpg"):
            return Response(url=url, content=b"jpeg")
        raise AssertionError(url)


class DemoGattsu(GattsuSource):
    name = "gattsu_pt_br"
    display_name = "Gattsu"
    base_url = "https://gattsu.test"
    language = "pt-BR"


class GattsuTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = DemoGattsu(Fetcher())
        series = (await source.browse("latest"))[0]
        chapter = (await source.chapters(series))[0]
        page = (await source.pages(chapter))[0]
        self.assertEqual(page.source_id, "https://gattsu.test/page.jpg")
        self.assertEqual(b"".join((await source.page_bytes(page)).chunks), b"jpeg")


if __name__ == "__main__":
    unittest.main()
