from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.generate import _manual_bundle


class Response:
    def __init__(self, url, text): self.url, self.text, self.status_code = url, text, 200
    def json(self): return json.loads(self.text)
    def raise_for_status(self): pass


class Fetcher:
    def __init__(self, responses): self.responses, self.requests = responses, []
    async def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs)); return self.responses.pop(0)


def source_class():
    path = Path(__file__).parents[1] / "engines" / "manual" / "catmanhwas_es.py"
    namespace = {"__name__": "test_catmanhwas_bundle"}
    exec(compile(_manual_bundle(path), str(path), "exec"), namespace)
    return namespace["SOURCE"]


class CatManhwasTest(unittest.IsolatedAsyncioTestCase):
    async def test_discovers_svelte_chunks_and_reads_paginated_chapters(self):
        html = """
        /_app/remote/details-hash/getSerieDetails
        /_app/remote/chapters-hash/getChapters
        """
        devalue = [
            {"data": 1, "pagination": 2}, [3],
            {"current_page": 4, "last_page": 4},
            {"id": 5, "number": 6, "name": 7, "published_at": 8},
            1, 42, 9.0, "Final", "2026-01-01T00:00:00Z",
        ]
        fetcher = Fetcher([
            Response("https://newcat1.xyz/series/demo", html),
            Response("https://newcat1.xyz/_app/remote/chapters-hash/getChapters", json.dumps({"result": json.dumps(devalue)})),
        ])
        source = source_class()(fetcher)

        chapters = await source.chapters("demo")

        self.assertEqual(chapters[0].source_id, "demo/42")
        self.assertEqual(chapters[0].title, "Capítulo 9: Final")
        self.assertIn("chapters-hash", fetcher.requests[1][1])
        self.assertIn("payload", fetcher.requests[1][2]["params"])


if __name__ == "__main__": unittest.main()
