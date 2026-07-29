from __future__ import annotations

import unittest

from engines.kemono import KemonoSource


class Response:
    def __init__(self, *, url="https://kemono.test/", data=None, content=b""):
        self.url = url
        self._data = data if data is not None else {}
        self.content = content
        self.headers = {"Content-Type": "image/jpeg"}

    def json(self):
        return self._data

    def raise_for_status(self):
        return None


class Fetcher:
    async def request(self, _method, url, **_kwargs):
        if url.endswith("/creators"):
            return Response(url=url, data=[{"id": "1", "name": "One", "service": "patreon", "favorited": 2}])
        if url.endswith("/patreon/user/1/posts"):
            return Response(url=url, data=[{"id": "7", "service": "patreon", "user": "1", "title": "Post", "file": {"path": "/a/1.jpg", "name": "1.jpg"}, "attachments": []}])
        if url.endswith("/patreon/user/1/post/7"):
            return Response(url=url, data={"post": {"file": {"path": "/a/1.jpg", "name": "1.jpg"}, "attachments": []}})
        if "/data/a/1.jpg" in url:
            return Response(url=url, content=b"jpeg")
        raise AssertionError(url)


class DemoKemono(KemonoSource):
    name = "kemono_all"
    display_name = "Kemono"
    base_url = "https://kemono.test"
    language = "all"


class KemonoTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = DemoKemono(Fetcher())
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        page = (await source.pages(chapter))[0]
        self.assertEqual(b"".join((await source.page_bytes(page)).chunks), b"jpeg")


if __name__ == "__main__":
    unittest.main()
