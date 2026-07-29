from __future__ import annotations

import unittest

from engines.vercomics import VerComicsSource


class Response:
    def __init__(self, text="", *, url="https://ver.test/", content=b""):
        self.text = text
        self.url = url
        self.content = content
        self.headers = {"Content-Type": "image/jpeg"}

    def raise_for_status(self):
        return None


class Fetcher:
    async def request(self, _method, url, **_kwargs):
        if "/page/" in url:
            return Response('<div class="entry"><a class="popimg" href="/one"><img alt="One"></a></div>', url=url)
        if url.endswith("/one"):
            return Response('<div class="wp-content"><p><img data-src="/1.jpg"></p></div>', url=url)
        if url.endswith("/1.jpg"):
            return Response(url=url, content=b"jpeg")
        raise AssertionError(url)


class DemoVer(VerComicsSource):
    name = "ver_es"
    display_name = "Ver"
    base_url = "https://ver.test"
    language = "es"
    url_suffix = "porno"


class VerComicsTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = DemoVer(Fetcher())
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        page = (await source.pages(chapter))[0]
        self.assertEqual(b"".join((await source.page_bytes(page)).chunks), b"jpeg")


if __name__ == "__main__":
    unittest.main()
