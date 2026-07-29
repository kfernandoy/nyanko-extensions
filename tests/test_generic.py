from __future__ import annotations

import unittest

from engines.generic import GenericSource


class Response:
    status_code = 200
    url = "https://demo.test/search"
    text = ""

    def json(self):
        return {"data": [{"id": "/series/1", "title": "Serie"}]}


class Fetcher:
    async def request(self, method, url, **kwargs):
        return Response()


class Demo(GenericSource):
    name = "demo_en"
    base_url = "https://demo.test"


class GenericSourceTest(unittest.IsolatedAsyncioTestCase):
    async def test_json_search(self):
        result = await Demo(Fetcher()).search("serie")
        self.assertEqual((result[0].title, result[0].source_id), ("Serie", "https://demo.test/series/1"))


if __name__ == "__main__":
    unittest.main()
