from __future__ import annotations

import unittest

from engines.masonry import MasonrySource


class Response:
    def __init__(self, text="", *, url="https://gallery.test/", content=b""):
        self.text = text
        self.url = url
        self.content = content
        self.headers = {"Content-Type": "image/jpeg"}

    def raise_for_status(self):
        return None


class Fetcher:
    async def request(self, _method, url, **_kwargs):
        if url == "https://gallery.test":
            return Response(
                '<div class="list-gallery"><figure><a href="/gallery/one/" title="One">'
                "</a></figure></div>"
            )
        if url.endswith("/gallery/one/"):
            return Response(
                '<div class="list-gallery"><a href="https://cdn.gallery.test/1.jpg"></a></div>',
                url=url,
            )
        if url.endswith("/1.jpg"):
            return Response(url=url, content=b"jpeg")
        raise AssertionError(url)


class DemoMasonry(MasonrySource):
    name = "gallery_all"
    display_name = "Gallery"
    base_url = "https://gallery.test"
    language = "all"


class MasonryTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = DemoMasonry(Fetcher())
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        pages = await source.pages(chapter)
        self.assertEqual(pages[0].source_id, "https://cdn.gallery.test/1.jpg")
        self.assertEqual(b"".join((await source.page_bytes(pages[0])).chunks), b"jpeg")


if __name__ == "__main__":
    unittest.main()
