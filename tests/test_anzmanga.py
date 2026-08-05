from __future__ import annotations

import unittest
from pathlib import Path

from tools.generate import _manual_bundle


class Response:
    def __init__(self, url: str, text: str) -> None:
        self.url, self.text, self.status_code = url, text, 200

    def raise_for_status(self) -> None: pass


class Fetcher:
    def __init__(self, responses): self.responses, self.requests = responses, []

    async def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return self.responses.pop(0)


def source_class():
    path = Path(__file__).parents[1] / "engines" / "manual" / "anzmanga_es.py"
    namespace = {"__name__": "test_anzmanga_bundle"}
    exec(compile(_manual_bundle(path), str(path), "exec"), namespace)
    return namespace["SOURCE"]


class AnzMangaTest(unittest.IsolatedAsyncioTestCase):
    async def test_popular_route_and_exact_listing_selector(self):
        html = """
        <div class="col-sm-6"><div class="media"><img src="/cover.jpg">
        <div class="media-heading"><a href="/manga/demo">Demo</a></div></div></div>
        <ul class="pagination"><li><a rel="next">Next</a></li></ul>
        """
        fetcher = Fetcher([Response("https://www.anzmanga25.com/filterList?page=2", html)])
        source = source_class()(fetcher)

        result = await source.browse("popular", 2)

        self.assertEqual(result["items"][0].title, "Demo")
        self.assertTrue(result["has_more"])
        self.assertEqual(fetcher.requests[0][2]["params"]["sortBy"], "views")


if __name__ == "__main__": unittest.main()
