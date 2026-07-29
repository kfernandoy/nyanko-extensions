from __future__ import annotations

import unittest

from engines.mangacatalog import MangaCatalogSource


class Response:
    def __init__(self, text="", *, url="https://catalog.test/", content=b""):
        self.text = text
        self.url = url
        self.content = content
        self.headers = {"Content-Type": "image/png"}

    def raise_for_status(self):
        return None


class Fetcher:
    async def request(self, _method, url, **_kwargs):
        if url.endswith("/manga/serie/"):
            return Response(
                '<div class="w-full"><div class="bg-bg-secondary"><div class="grid">'
                '<div class="col-span-4"><a href="/chapter/1/">Chapter 1</a>'
                '<div class="text-xs">Título</div></div></div></div></div>',
                url=url,
            )
        if url.endswith("/chapter/1/"):
            return Response('<img data-src="/pages/1.png">', url=url)
        if url.endswith("/pages/1.png"):
            return Response(url=url, content=b"png")
        raise AssertionError(url)


class DemoCatalog(MangaCatalogSource):
    name = "catalog_en"
    display_name = "Catalog"
    base_url = "https://catalog.test"
    language = "en"
    source_list = (("Serie", "https://catalog.test/manga/serie/"),)


class MangaCatalogTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = DemoCatalog(Fetcher())
        series = (await source.search("serie"))[0]
        chapters = await source.chapters(series)
        self.assertEqual(chapters[0].title, "Chapter 1 - Título")
        pages = await source.pages(chapters[0])
        self.assertEqual(pages[0].source_id, "https://catalog.test/pages/1.png")
        self.assertEqual(b"".join((await source.page_bytes(pages[0])).chunks), b"png")


if __name__ == "__main__":
    unittest.main()
