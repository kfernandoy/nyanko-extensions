from __future__ import annotations

import unittest
from pathlib import Path

from tools.generate import _manual_bundle


class Response:
    def __init__(self, url: str, text: str) -> None:
        self.url = url
        self.text = text
        self.status_code = 200

    def raise_for_status(self) -> None:
        pass


class Fetcher:
    def __init__(self, responses: list[Response]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, str, dict]] = []

    async def request(self, method: str, url: str, **kwargs):
        self.requests.append((method, url, kwargs))
        return self.responses.pop(0)


def source_class():
    path = Path(__file__).parents[1] / "engines" / "manual" / "akaya_es.py"
    namespace = {"__name__": "test_akaya_bundle"}
    exec(compile(_manual_bundle(path), str(path), "exec"), namespace)
    return namespace["SOURCE"]


class AkayaTest(unittest.IsolatedAsyncioTestCase):
    async def test_popular_and_chapter_images_match_kotlin_routes(self):
        browse_html = """
        <div class="serie_items"><div class="library-grid-item">
          <a href="/serie/demo"><div class="inner-img" style="background:url('/cover.jpg')"></div>
          <span><h5><strong>Demo</strong></h5></span></a>
        </div></div><a rel="next" href="?page=2">Siguiente</a>
        """
        pages_html = """<script>var chapterData = {"images":[
          {"image":"second.jpg","order_sort":2},{"image":"first.jpg","order_sort":1}
        ]};</script>"""
        fetcher = Fetcher(
            [
                Response("https://akaya.io/collection/popular?page=1", browse_html),
                Response("https://akaya.io/chapter/demo", pages_html),
            ]
        )
        source = source_class()(fetcher)

        browse = await source.browse("popular", 1)
        pages = await source.pages("https://akaya.io/chapter/demo")

        self.assertEqual(browse["items"][0].title, "Demo")
        self.assertTrue(browse["has_more"])
        self.assertIn("bd90cb43-9bf2-4759-b8cc-c9e66a526bc6", fetcher.requests[0][1])
        self.assertEqual(
            [page.source_id for page in pages],
            [
                "https://api.akayamedia.com/chapters/first.jpg",
                "https://api.akayamedia.com/chapters/second.jpg",
            ],
        )


if __name__ == "__main__":
    unittest.main()
