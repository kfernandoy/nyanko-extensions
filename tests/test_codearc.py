from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.generate import _manual_bundle


class Response:
    def __init__(self, url, payload):
        self.url, self.status_code = url, 200
        self.text = payload if isinstance(payload, str) else json.dumps(payload)
    def json(self): return json.loads(self.text)
    def raise_for_status(self): pass


class Fetcher:
    def __init__(self, responses): self.responses, self.requests = responses, []
    async def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs)); return self.responses.pop(0)


def source_class():
    path = Path(__file__).parents[1] / "engines" / "manual" / "codearc_es.py"
    namespace = {"__name__": "test_codearc_bundle"}
    exec(compile(_manual_bundle(path), str(path), "exec"), namespace)
    return namespace["SOURCE"]


class CodeArcTest(unittest.IsolatedAsyncioTestCase):
    async def test_genres_come_from_codearc_links(self):
        fetcher = Fetcher([Response(
            "https://mangas.codearctraducciones.com/list",
            '<a href="/list?generos=accion">Acción</a><a href="/list?generos=drama">Drama</a>',
        )])
        source = source_class()(fetcher)

        filters = await source.get_filters()

        self.assertEqual(filters[-1].options, [("accion", "Acción"), ("drama", "Drama")])

    async def test_next_reader_fetches_remaining_pages(self):
        reader = {"initialPages": [{"imagen_url": "https://cdn/1.jpg"}], "totalPages": 2, "pagesFetchUrl": "/api/pages"}
        fetcher = Fetcher([
            Response("https://mangas.codearctraducciones.com/reader/demo/1/cascade", f"1:{json.dumps(reader)}"),
            Response("https://mangas.codearctraducciones.com/api/pages", {"items": [{"imagen_url": "https://cdn/2.jpg"}]}),
        ])
        source = source_class()(fetcher)

        pages = await source.pages("https://mangas.codearctraducciones.com/reader/demo/1/cascade")

        self.assertEqual([page.source_id for page in pages], ["https://cdn/1.jpg", "https://cdn/2.jpg"])
        self.assertEqual(fetcher.requests[0][2]["headers"], {"RSC": "1"})
        self.assertEqual(fetcher.requests[1][2]["params"], {"offset": "1"})


if __name__ == "__main__": unittest.main()
