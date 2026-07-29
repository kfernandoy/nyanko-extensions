from __future__ import annotations

import base64
import json
import unittest

from engines.goda import GodaSource, _decode_chapter_images


class Response:
    def __init__(self, text="", *, url="https://goda.test/", content=b""):
        self.text = text
        self.url = url
        self.content = content
        self.headers = {"Content-Type": "image/jpeg"}

    def raise_for_status(self):
        return None


class Fetcher:
    async def request(self, _method, url, **_kwargs):
        if "/hots/" in url:
            return Response(
                '<div class="pb-2"><a href="/manga/one"><h3>One</h3></a></div>',
                url=url,
            )
        if url.endswith("/manga/one"):
            return Response('<div id="mangachapters" data-mid="7"></div>', url=url)
        if url.endswith("/manga/get"):
            return Response(
                '<div class="chapteritem"><a href="/manga/one/ch2" '
                'data-cs="8" data-ct="Chapter 2"></a></div>',
                url=url,
            )
        if url.endswith("/chapter/getcontent"):
            return Response('<div id="chapcontent"><div><img data-src="/1.jpg"></div></div>', url=url)
        if url.endswith("/1.jpg"):
            return Response(url=url, content=b"jpeg")
        raise AssertionError(url)


class DemoGoda(GodaSource):
    name = "goda_en"
    display_name = "Goda"
    base_url = "https://goda.test"
    language = "en"


def encode_images(value):
    standard = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    custom = "_-9876543210abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    raw = base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")
    mapped = raw.translate(str.maketrans(standard, custom))
    chunks = [mapped[index : index + 7] for index in range(0, len(mapped), 7)]
    zigzag = "".join(chunk[::-1] if index % 2 else chunk for index, chunk in enumerate(chunks))
    a_length = len(zigzag) // 3
    b_length = (len(zigzag) - a_length) // 2
    part3, part1, part2 = zigzag[:a_length], zigzag[a_length : a_length + b_length], zigzag[a_length + b_length :]
    return f"J7r{part1}kD{part2}W4s{part3}nQ"


class GodaTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_flow(self):
        source = DemoGoda(Fetcher())
        series = (await source.browse("popular"))[0]
        chapter = (await source.chapters(series))[0]
        pages = await source.pages(chapter)
        self.assertEqual(b"".join((await source.page_bytes(pages[0])).chunks), b"jpeg")

    def test_image_decoder(self):
        rows = [{"url": "/page.jpg", "order": 1}]
        self.assertEqual(_decode_chapter_images(encode_images(rows)), rows)


if __name__ == "__main__":
    unittest.main()
