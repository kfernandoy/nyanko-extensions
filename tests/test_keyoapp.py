from __future__ import annotations

import unittest

from engines.keyoapp import KeyoappSource


class Response:
    def __init__(self, text="", *, url="https://keyo.test/", content=b""):
        self.text = text
        self.url = url
        self.content = content
        self.headers = {"Content-Type": "image/jpeg"}

    def raise_for_status(self):
        return None


class Fetcher:
    async def request(self, _method, url, **_kwargs):
        if url == "https://keyo.test":
            return Response(
                '<div class="grid"><div class="group grid"><a href="/series/one/" '
                'title="One"></a></div></div>'
            )
        if url.endswith("/series/one/"):
            return Response(
                '<div id="chapters"><a href="/chapter/1/"><span class="text-sm">'
                "Chapter 1</span></a></div>",
                url=url,
            )
        if url.endswith("/chapter/1/"):
            return Response(
                '<div id="pages"><img uid="one.jpg"></div>'
                "<script>realUrl = `https://cdn.keyo.test/path`</script>",
                url=url,
            )
        if url.endswith("/chapter/ld/"):
            return Response(
                '<script type="application/ld+json">'
                '{"url":"https://keyo.test/chapter/ld","numberOfPages":2,'
                '"isPartOf":{"url":"https://keyo.test/series/one"}}</script>',
                url=url,
            )
        if url.endswith("/uploads/one.jpg"):
            return Response(url=url, content=b"jpeg")
        raise AssertionError(url)


class DemoKeyoapp(KeyoappSource):
    name = "keyo_en"
    display_name = "Keyo"
    base_url = "https://keyo.test"
    language = "en"


class LDKeyoapp(DemoKeyoapp):
    pages_profile = "ld_json"


class KeyoappTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = DemoKeyoapp(Fetcher())
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        pages = await source.pages(chapter)
        self.assertEqual(pages[0].source_id, "https://cdn.keyo.test/uploads/one.jpg")
        self.assertEqual(b"".join((await source.page_bytes(pages[0])).chunks), b"jpeg")
        ld_pages = await LDKeyoapp(Fetcher()).pages("https://keyo.test/chapter/ld/")
        self.assertEqual(
            ld_pages[-1].source_id,
            "https://keyo.test/storage/series/webtoon/one/chapters/ld/002.jpg",
        )


if __name__ == "__main__":
    unittest.main()
