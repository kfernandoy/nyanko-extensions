import unittest

from engines.greenshit import GreenShitSource


class Response:
    def __init__(self, data=None, content=b""):
        self.data, self.content = data or {}, content
        self.headers = {"Content-Type": "image/jpeg"}

    def json(self):
        return self.data

    def raise_for_status(self):
        pass


class Fetcher:
    async def request(self, _method, url, **_kwargs):
        if url.endswith("/obras/ranking"):
            return Response({"obras": [{"obr_id": 1, "obr_nome": "One"}]})
        if url.endswith("/obras/1"):
            return Response({"capitulos": [{"cap_id": 2, "cap_nome": "Two", "cap_numero": 2}]})
        if url.endswith("/capitulos/2"):
            return Response({"obra": {"obr_id": 1, "scan_id": 3}, "cap_numero": 2, "cap_paginas": [{"src": "1.jpg"}]})
        if url.endswith("/scans/3/obras/1/capitulos/2/1.jpg"):
            return Response(content=b"jpg")
        raise AssertionError(url)


class Demo(GreenShitSource):
    name, display_name = "green_pt_br", "Green"
    base_url, language = "https://green.test", "pt-BR"
    api_url, cdn_url, scan_id = "https://api.green.test", "https://cdn.green.test", "3"


class GreenShitTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = Demo(Fetcher())
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        page = (await source.pages(chapter))[0]
        self.assertEqual(b"".join((await source.page_bytes(page)).chunks), b"jpg")


if __name__ == "__main__":
    unittest.main()
