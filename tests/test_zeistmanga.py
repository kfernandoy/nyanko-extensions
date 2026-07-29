from __future__ import annotations

import unittest

from engines.zeistmanga import ZeistMangaSource


class Response:
    def __init__(self, payload=None, *, text="", url="https://zeist.test/", content=b""):
        self._payload = payload or {}
        self.text = text
        self.url = url
        self.content = content
        self.headers = {"Content-Type": "image/jpeg"}

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class Fetcher:
    async def request(self, _method, url, **kwargs):
        if "/feeds/posts/default/-/Series" in url:
            return Response(
                {
                    "feed": {
                        "entry": [
                            {
                                "title": {"$t": "Latest"},
                                "category": [{"term": "Series"}],
                                "link": [{"rel": "alternate", "href": "https://zeist.test/latest/"}],
                            }
                        ]
                    }
                }
            )
        if url == "https://zeist.test":
            return Response(
                text='<div class="PopularPosts"><figure><figcaption>'
                '<a href="/series/one/">One</a></figcaption></figure></div>'
            )
        if url.endswith("/series/one/"):
            return Response(text="<script>clwd.run('one')</script>", url=url)
        if "/feeds/posts/default/-/Chapter/one" in url:
            return Response(
                {
                    "feed": {
                        "entry": [
                            {
                                "title": {"$t": "Chapter 2"},
                                "category": [{"term": "Chapter"}],
                                "link": [{"rel": "alternate", "href": "https://zeist.test/c/2/"}],
                            }
                        ]
                    }
                }
            )
        if url.endswith("/c/2/"):
            return Response(
                text='<div class="check-box"><div class="separator">'
                '<img src="/p/1.jpg"></div></div>',
                url=url,
            )
        if url.endswith("/p/1.jpg"):
            return Response(url=url, content=b"jpeg")
        raise AssertionError((url, kwargs))


class DemoZeist(ZeistMangaSource):
    name = "zeist_es"
    display_name = "Zeist"
    base_url = "https://zeist.test"
    language = "es"


class ComicVerseZeist(DemoZeist):
    chapter_feed_profile = "comicverse"


class YuriMoonZeist(DemoZeist):
    chapter_feed_profile = "yurimoon"


class LatestAsPopularZeist(DemoZeist):
    popular_is_latest = True


class ZeistMangaTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = DemoZeist(Fetcher())
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        pages = await source.pages(chapter)
        self.assertEqual(pages[0].source_id, "https://zeist.test/p/1.jpg")
        self.assertEqual(b"".join((await source.page_bytes(pages[0])).chunks), b"jpeg")
        self.assertEqual(
            ComicVerseZeist(Fetcher())._chapter_feed(
                '<div class="manga-widget" data-label="Hero"></div>'
            ),
            ("Chapter", "Hero"),
        )
        self.assertEqual(
            YuriMoonZeist(Fetcher())._chapter_feed(
                "<script>clwd.run('%D8%A8%D8%A7%D8%A8  Hero')</script>"
            ),
            ("Chapter", "Hero"),
        )
        self.assertEqual(
            (await LatestAsPopularZeist(Fetcher()).browse("popular"))[0].title,
            "Latest",
        )


if __name__ == "__main__":
    unittest.main()
