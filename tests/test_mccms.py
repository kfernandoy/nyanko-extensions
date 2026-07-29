import unittest

from engines.mccms import MCCMSSource


class Response:
    def __init__(self, data=None, text="", content=b""):
        self.data, self.text, self.content = data or {}, text, content
        self.headers = {"Content-Type": "image/jpeg"}

    def json(self):
        return self.data

    def raise_for_status(self):
        pass


class Fetcher:
    async def request(self, _method, url, **_kwargs):
        if url.endswith("/api/data/comic"):
            return Response({"data": [{"id": "1", "name": "One", "url": "/comic/one"}]})
        if url.endswith("/api/comic/chapter"):
            return Response({"data": [{"name": "Two", "link": "/chapter/two"}]})
        if url.endswith("/chapter/two"):
            return Response(text='<img data-original="https://cdn.test/1.jpg">')
        if url == "https://cdn.test/1.jpg":
            return Response(content=b"jpg")
        raise AssertionError(url)


class Demo(MCCMSSource):
    name, display_name = "mccms_zh", "MCCMS"
    base_url, language = "https://mccms.test", "zh"


class MCCMSTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = Demo(Fetcher())
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        page = (await source.pages(chapter))[0]
        self.assertEqual(b"".join((await source.page_bytes(page)).chunks), b"jpg")


if __name__ == "__main__":
    unittest.main()
