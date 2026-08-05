from __future__ import annotations

import unittest
from pathlib import Path

from tools.generate import _manual_bundle


class Response:
    def __init__(self, url: str, text: str = "", content: bytes = b"") -> None:
        self.url = url
        self.text = text
        self.content = content
        self.headers = {"Content-Type": "image/jpeg"}
        self.status_code = 200

    def raise_for_status(self) -> None:
        pass


class Fetcher:
    def __init__(self, responses: list[Response]) -> None:
        self.responses = responses
        self.requests = []

    async def request(self, method: str, url: str, **kwargs):
        self.requests.append((method, url, kwargs))
        return self.responses.pop(0)


def source_class():
    path = Path(__file__).parents[1] / "engines" / "manual" / "akuma_es.py"
    namespace = {"__name__": "test_akuma_bundle"}
    exec(compile(_manual_bundle(path), str(path), "exec"), namespace)
    return namespace["SOURCE"]


class AkumaTest(unittest.IsolatedAsyncioTestCase):
    async def test_csrf_cursor_language_and_image_page_match_kotlin(self):
        listing = """
        <div class="post-loop"><li><a href="/g/42"><span class="overlay-title">Demo [ES]</span>
        <img src="/cover.jpg"></a></li></div>
        <div class="page-item"><a rel="next" href="/?cursor=abc">Next</a></div>
        """
        fetcher = Fetcher([
            Response("https://akuma.moe", '<meta name="csrf-token" content="token">'),
            Response("https://akuma.moe", listing),
            Response("https://akuma.moe/g/42/1", '<div class="nav-select"><option value="1">1</option></div>'),
            Response("https://akuma.moe/g/42/1", '<div class="entry-content"><img src="/image.jpg"></div>'),
            Response("https://akuma.moe/image.jpg", content=b"image"),
        ])
        source = source_class()(fetcher)

        browse = await source.browse("popular", 1)
        pages = await source.pages("https://akuma.moe/g/42/1")
        content = await source.page_bytes(pages[0])

        self.assertEqual(browse["items"][0].title, "Demo")
        self.assertTrue(browse["has_more"])
        self.assertEqual(fetcher.requests[1][2]["params"]["q"], "language:spanish$")
        self.assertEqual(fetcher.requests[1][2]["headers"]["X-CSRF-TOKEN"], "token")
        self.assertEqual(b"".join(content.chunks), b"image")


if __name__ == "__main__":
    unittest.main()
