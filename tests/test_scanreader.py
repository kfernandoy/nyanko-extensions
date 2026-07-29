from __future__ import annotations

import base64
import unittest

from engines.scanreader import ScanReaderSource


class Response:
    def __init__(self, text="", *, url="https://scan.test/", content=b""):
        self.text = text
        self.url = url
        self.content = content
        self.headers = {"Content-Type": "image/jpeg"}

    def raise_for_status(self):
        return None


class Fetcher:
    async def request(self, method, url, **_kwargs):
        if url == "https://scan.test" and method == "GET":
            return Response('<div class="manga-card"><a href="/manga/one"><h3>One</h3></a></div>', url=url)
        if url.endswith("/manga/one"):
            return Response('<div id="secure-chapters-container" data-manga-id="7" data-nonce="n"></div>', url=url)
        if url.endswith("/admin-ajax.php"):
            return Response('{"data":"<div><a href=\\"/chapitre/1\\"></a><h4>Chapitre 1</h4></div>"}', url=url)
        if url.endswith("/chapitre/1"):
            encoded = base64.b64encode("https://scan.test/1.jpg"[::-1].encode()).decode()
            return Response(f'const pages = ["{encoded}"];', url=url)
        if url.endswith("/1.jpg"):
            return Response(url=url, content=b"jpeg")
        raise AssertionError(url)


class DemoScanReader(ScanReaderSource):
    name = "scan_fr"
    display_name = "Scan"
    base_url = "https://scan.test"
    language = "fr"


class ScanReaderTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = DemoScanReader(Fetcher())
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        page = (await source.pages(chapter))[0]
        self.assertEqual(b"".join((await source.page_bytes(page)).chunks), b"jpeg")


if __name__ == "__main__":
    unittest.main()
