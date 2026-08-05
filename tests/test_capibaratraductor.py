from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.generate import _manual_bundle


class Response:
    def __init__(self, url, payload): self.url, self.text, self.status_code = url, json.dumps(payload), 200
    def json(self): return json.loads(self.text)
    def raise_for_status(self): pass


class Fetcher:
    def __init__(self, responses): self.responses, self.requests = responses, []
    async def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs)); return self.responses.pop(0)


def source_class():
    path = Path(__file__).parents[1] / "engines" / "manual" / "capibaratraductor_es.py"
    namespace = {"__name__": "test_capibara_bundle"}
    exec(compile(_manual_bundle(path), str(path), "exec"), namespace)
    return namespace["SOURCE"]


class CapibaraTraductorTest(unittest.IsolatedAsyncioTestCase):
    async def test_dynamic_scans_and_series_identity_match_api(self):
        scans = lambda item: {"data": {"items": [item], "page": 1, "maxPage": 1}}
        listing = {"data": {"items": [{
            "title": "Demo", "imageUrl": "https://img/cover.jpg",
            "manga": {"slug": "demo"},
            "organization": {"slug": "scan", "name": "Scan"},
        }], "maxPage": 2}}
        fetcher = Fetcher([
            Response("https://capibaratraductor.com/api/landing/scans", scans({"id": "safe", "name": "Safe"})),
            Response("https://capibaratraductor.com/api/landing/scans", scans({"id": "adult", "name": "Adult"})),
            Response("https://capibaratraductor.com/api/manga-custom", listing),
        ])
        source = source_class()(fetcher)

        filters = await source.get_filters()
        result = await source.search("Demo", 1, {"scanlator": "scan", "order": "popular"})

        self.assertEqual(filters[0].options[1:], [("adult", "Adult"), ("safe", "Safe")])
        self.assertEqual(result["items"][0].source_id, "demo/scan")
        self.assertTrue(result["has_more"])
        self.assertEqual(fetcher.requests[2][2]["headers"], {"x-organization": "scan"})


if __name__ == "__main__": unittest.main()
