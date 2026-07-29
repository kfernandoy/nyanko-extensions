from __future__ import annotations

import unittest

from engines.madtheme import MadThemeSource


class Response:
    def __init__(self, text="", *, url="https://mad.test/", content=b""):
        self.text = text
        self.url = url
        self.content = content
        self.headers = {"Content-Type": "image/jpeg"}

    def raise_for_status(self):
        return None


class FailingResponse(Response):
    def raise_for_status(self):
        raise RuntimeError("CDN failed")


class Fetcher:
    async def request(self, _method, url, **_kwargs):
        if url.endswith("/search"):
            return Response(
                '<div class="book-detailed-item">'
                '<a href="/manga/one" title="One"></a></div>',
                url=url,
            )
        if url.endswith("/api/manga/one/chapters"):
            return Response(
                '<ul id="chapter-list"><li><a href="/chapter/4">'
                '<span class="chapter-title">Chapter 4</span></a></li></ul>',
                url=url,
            )
        if url.endswith("/chapter/4"):
            return Response(
                '<div id="chapter-images"><img data-src="/bad.jpg" '
                """onerror="this.src='/good.jpg'"></div>""",
                url=url,
            )
        if url.endswith("/bad.jpg"):
            return FailingResponse(url=url)
        if url.endswith("/good.jpg"):
            return Response(url=url, content=b"jpeg")
        raise AssertionError(url)


class DemoMadTheme(MadThemeSource):
    name = "mad_en"
    display_name = "Mad"
    base_url = "https://mad.test"
    language = "en"
    use_slug_search = True


class MadThemeTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow_and_image_fallback(self):
        source = DemoMadTheme(Fetcher())
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        pages = await source.pages(chapter)
        self.assertIn("||fallback=", pages[0].source_id)
        self.assertEqual(b"".join((await source.page_bytes(pages[0])).chunks), b"jpeg")


if __name__ == "__main__":
    unittest.main()
