from __future__ import annotations

import unittest

from engines.grouple import GroupLeSource


class Response:
    def __init__(self, text="", *, url="https://group.test/", content=b""):
        self.text = text
        self.url = url
        self.content = content
        self.headers = {"Content-Type": "image/jpeg"}

    def raise_for_status(self):
        return None


class Fetcher:
    async def request(self, _method, url, **_kwargs):
        if url.endswith("/list"):
            return Response(
                '<div class="tile"><h3><a href="/title" title="Title"></a></h3></div>',
                url=url,
            )
        if url.endswith("/title"):
            return Response(
                '<tr class="item-row"><td class="item-title" data-num="25">'
                '<a class="chapter-link" href="/chapter" title="Team (Переводчик)">'
                "Chapter 2.5</a></td></tr>",
                url=url,
            )
        if "/chapter?mtr=true" in url:
            return Response(
                """rm_h.readerInit('01.jpg','//cdn.test/images/',"/pages/");""",
                url=url,
            )
        if url == "https://cdn.test/images/01.jpg/pages/":
            return Response(url=url, content=b"jpeg")
        raise AssertionError(url)


class DemoGroupLe(GroupLeSource):
    name = "group_ru"
    display_name = "Group"
    base_url = "https://group.test"
    language = "ru"


class GroupLeTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = DemoGroupLe(Fetcher())
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        self.assertEqual(chapter.number, 2.5)
        pages = await source.pages(chapter)
        self.assertEqual(pages[0].source_id, "https://cdn.test/images/01.jpg/pages/")
        self.assertEqual(b"".join((await source.page_bytes(pages[0])).chunks), b"jpeg")


if __name__ == "__main__":
    unittest.main()
