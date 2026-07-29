from __future__ import annotations

import unittest

from engines.comiciviewer import ComiciViewerSource


class Response:
    def __init__(self, payload):
        self._payload = payload
        self.text = ""
        self.url = "https://demo.test/"

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class Fetcher:
    async def request(self, method, url, **kwargs):
        if url.endswith("/search"):
            return Response({"searchResult": {"series": {"series": [{"id": "abc", "name": "Serie"}]}}})
        if url.endswith("/episodes"):
            return Response({"series": {"episodes": [{"id": "2", "title": "Episode 2"}]}})
        if url.endswith("/episodes/2"):
            return Response({"episode": {"contentId": 7, "content": [{"type": "viewer", "viewerId": "v"}]}})
        return Response({"result": [{"imageUrl": "https://cdn.test/1.jpg", "scramble": "[]", "sort": 1}]})


class Demo(ComiciViewerSource):
    name = "demo_ja"
    base_url = "https://demo.test"
    api_url = "https://demo.test/api"


class ComiciViewerTest(unittest.IsolatedAsyncioTestCase):
    async def test_api_flow(self):
        source = Demo(Fetcher())
        series = (await source.search("serie"))[0]
        chapter = (await source.chapters(series))[0]
        page = (await source.pages(chapter))[0]
        self.assertEqual((series.title, chapter.number, page.index), ("Serie", 2.0, 1))


if __name__ == "__main__":
    unittest.main()
