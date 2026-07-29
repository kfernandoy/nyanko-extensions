from __future__ import annotations

import base64
import unittest

from engines.mangathemesia import MangaThemesiaSource


class Response:
    def __init__(self, text="", *, url="https://theme.test/", content=b""):
        self.text = text
        self.url = url
        self.status_code = 200
        self.content = content
        self.headers = {"Content-Type": "image/webp"}

    def raise_for_status(self):
        return None


class Fetcher:
    async def request(self, _method, url, **kwargs):
        if url.endswith("/manga/"):
            return Response(
                '<div class="listupd"><div class="bs"><div class="bsx">'
                '<a href="/manga/serie/" title="Serie"></a></div></div></div>'
            )
        if url.endswith("/manga/serie/"):
            return Response(
                '<div id="chapterlist"><ul><li><div class="eph-num">'
                '<a href="/chapter/3/"><span class="chapternum">Chapter 3</span></a>'
                "</div></li></ul></div>",
                url=url,
            )
        if url.endswith("/chapter/3/"):
            return Response(
                '<div id="readerarea"><img data-src="/pages/3-1.webp"></div>',
                url=url,
            )
        if url.endswith("/chapter/encoded/"):
            script = 'ts_reader.run({"sources":[{"images":["/pages/encoded.webp"]}]})'
            encoded = base64.b64encode(script.encode()).decode()
            return Response(
                f'<script src="data:text/javascript;base64,{encoded}"></script>',
                url=url,
            )
        if url.endswith("/pages/3-1.webp"):
            return Response(url=url, content=b"webp")
        raise AssertionError(url)


class DemoMangaThemesia(MangaThemesiaSource):
    name = "theme_es"
    display_name = "Theme"
    base_url = "https://theme.test"
    language = "es"


class MangaThemesiaTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = DemoMangaThemesia(Fetcher())
        self.assertEqual((await source.browse("popular"))[0].title, "Serie")
        chapters = await source.chapters("https://theme.test/manga/serie/")
        self.assertEqual(chapters[0].number, 3)
        pages = await source.pages(chapters[0])
        self.assertEqual(pages[0].source_id, "https://theme.test/pages/3-1.webp")
        self.assertEqual(b"".join((await source.page_bytes(pages[0])).chunks), b"webp")
        encoded = await source.pages("https://theme.test/chapter/encoded/")
        self.assertEqual(encoded[0].source_id, "https://theme.test/pages/encoded.webp")
        self.assertEqual(
            source._unpack_packer(
                "eval(function(p,a,c,k,e,d){}('0 1',2,2,'hello|world'.split('|'),0,{}))"
            ),
            "hello world",
        )


if __name__ == "__main__":
    unittest.main()
