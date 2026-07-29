from __future__ import annotations

import unittest

from engines.madara import MadaraSource


class Response:
    def __init__(self, text="", *, url="https://demo.test/", status=200, content=b""):
        self.text = text
        self.url = url
        self.status_code = status
        self.content = content
        self.headers = {"Content-Type": "image/jpeg"}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class Fetcher:
    async def request(self, method, url, **kwargs):
        if kwargs.get("params", {}).get("post_type") == "wp-manga":
            return Response(
                '<div class="c-tabs-item__content"><div class="post-title">'
                '<a href="/manga/serie/">Serie</a></div></div>'
            )
        if url.endswith("/manga/serie/"):
            return Response(
                '<div id="manga-chapters-holder-1" data-id="7"></div>',
                url=url,
            )
        if kwargs.get("data", {}).get("action") == "manga_get_chapters":
            return Response(
                '<li class="wp-manga-chapter"><a href="/manga/serie/chapter-2/">'
                "Chapter 2</a></li>"
            )
        if "chapter-2" in url:
            return Response(
                '<div class="reading-content"><div class="page-break">'
                '<img data-src="/images/001.jpg"></div></div>',
                url=url,
            )
        if url.endswith("/images/001.jpg"):
            return Response(url=url, content=b"jpeg")
        return Response(
            '<div class="page-item-detail"><div class="post-title">'
            '<a href="/manga/serie/">Serie</a></div></div>',
            url=url,
        )


class DemoMadara(MadaraSource):
    name = "demo_es"
    display_name = "Demo"
    base_url = "https://demo.test"
    language = "es"


class MadaraTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = DemoMadara(Fetcher())
        self.assertEqual((await source.search("serie"))[0].title, "Serie")
        self.assertEqual((await source.browse("popular"))[0].source_id, "https://demo.test/manga/serie/")
        chapters = await source.chapters("https://demo.test/manga/serie/")
        self.assertEqual(chapters[0].number, 2)
        self.assertTrue(chapters[0].source_id.endswith("?style=list"))
        pages = await source.pages(chapters[0])
        self.assertEqual(pages[0].source_id, "https://demo.test/images/001.jpg")
        self.assertEqual(b"".join((await source.page_bytes(pages[0])).chunks), b"jpeg")

        source.chapter_url_suffix = ""
        self.assertFalse((await source.chapters("https://demo.test/manga/serie/"))[0].source_id.endswith("?style=list"))

    async def test_encoded_page_profiles(self):
        source = DemoMadara(Fetcher())
        source.pages_profile = "base64_pages"
        html = 'var pages = ["aHR0cHM6Ly9jZG4udGVzdC8xLmpwZw=="];'
        self.assertEqual(
            source._profile_page_urls(html, source.base_url),
            ["https://cdn.test/1.jpg"],
        )


if __name__ == "__main__":
    unittest.main()
