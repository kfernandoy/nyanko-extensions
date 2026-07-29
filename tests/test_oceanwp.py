from __future__ import annotations

import unittest

from engines.oceanwp import OceanWPSource


class Response:
    def __init__(self, text="", *, url="https://ocean.test/", content=b""):
        self.text = text
        self.url = url
        self.content = content
        self.headers = {"Content-Type": "image/jpeg"}

    def raise_for_status(self):
        return None


class Fetcher:
    async def request(self, _method, url, **_kwargs):
        if url == "https://ocean.test":
            return Response(
                '<article class="blog-entry"><h2 class="blog-entry-title">'
                '<a href="/one">One</a></h2></article>',
                url=url,
            )
        if url.endswith("/one"):
            return Response(
                '<div class="entry-content"><img src="/images/1.jpg"></div>',
                url=url,
            )
        if url.endswith("/images/1.jpg"):
            return Response(url=url, content=b"jpeg")
        raise AssertionError(url)


class DemoOcean(OceanWPSource):
    name = "ocean_id"
    display_name = "Ocean"
    base_url = "https://ocean.test"
    language = "id"


class OceanWPTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = DemoOcean(Fetcher())
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        pages = await source.pages(chapter)
        self.assertEqual(b"".join((await source.page_bytes(pages[0])).chunks), b"jpeg")


if __name__ == "__main__":
    unittest.main()
