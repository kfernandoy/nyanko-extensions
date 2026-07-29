from __future__ import annotations

import unittest

from engines.uzaymanga import UzayMangaSource


class Response:
    def __init__(self, payload=None, *, url="https://uzay.test/", content=b""):
        self._payload = payload
        self.url = url
        self.content = content
        self.headers = {"Content-Type": "image/jpeg"}

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def svelte(data):
    return {"type": "data", "nodes": [{"type": "data", "data": data}]}


class Fetcher:
    async def request(self, _method, url, **_kwargs):
        if url.endswith("/manga/__data.json"):
            return Response(svelte([{"series": 1}, [2], {"name": 3, "slug": 4}, "One", "one"]), url=url)
        if url.endswith("/manga/one/__data.json"):
            return Response(
                svelte(
                    [
                        {"series": 1},
                        {"slug": 2, "SeriesEpisode": 3},
                        "one",
                        [4],
                        {"slug": 5, "order": 6, "name": 7},
                        "chapter-9",
                        "9.0",
                        "Nine",
                    ]
                ),
                url=url,
            )
        if url.endswith("/manga/one/chapter-9/__data.json"):
            return Response(svelte([{"episode": 1}, {"images": 2}, [3], "/pages/1.jpg"]), url=url)
        if url.endswith("/pages/1.jpg"):
            return Response(url=url, content=b"jpeg")
        raise AssertionError(url)


class DemoUzay(UzayMangaSource):
    name = "uzay_tr"
    display_name = "Uzay"
    base_url = "https://uzay.test"
    cdn_url = "https://cdn.test"
    language = "tr"


class UzayMangaTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = DemoUzay(Fetcher())
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        pages = await source.pages(chapter)
        self.assertEqual(pages[0].source_id, "https://cdn.test/pages/1.jpg")
        self.assertEqual(b"".join((await source.page_bytes(pages[0])).chunks), b"jpeg")


if __name__ == "__main__":
    unittest.main()
