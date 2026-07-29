from __future__ import annotations

import unittest

from engines.stalkercms import StalkerCmsSource


class Response:
    def __init__(self, text="", *, url="https://stalker.test/", data=None, content=b""):
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
    async def request(self, _method, url, **_kwargs):
        if "/manga/todos/" in url:
            return Response('<a class="comic-card-link" href="/one"><h3>One</h3></a>', url=url)
        if url.endswith("/one"):
            return Response('<a class="chapter-link" href="/chapter/1"><span class="chapter-number">Capítulo 1</span></a>', url=url)
        if url.endswith("/chapter/1"):
            return Response('<canvas class="chapter-image-canvas" data-src-url="/1.jpg"></canvas>', url=url)
        if url.endswith("/1.jpg"):
            return Response(url=url, content=b"jpeg")
        raise AssertionError(url)


class DemoStalker(StalkerCmsSource):
    name = "stalker_pt"
    display_name = "Stalker"
    base_url = "https://stalker.test"
    language = "pt-BR"


class StalkerCmsTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = DemoStalker(Fetcher())
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        page = (await source.pages(chapter))[0]
        self.assertEqual(b"".join((await source.page_bytes(page)).chunks), b"jpeg")


if __name__ == "__main__":
    unittest.main()
