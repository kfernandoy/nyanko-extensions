from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.generate import _supported_zeistmanga, _zeistmanga_bundle


class Response:
    def __init__(self, url, payload):
        self.url, self.status_code = url, 200
        self.text = payload if isinstance(payload, str) else json.dumps(payload)

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        pass


class Fetcher:
    def __init__(self, responses):
        self.responses, self.requests = responses, []

    async def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return self.responses.pop(0)


def entry(index: int, category: str = "Chapter"):
    return {
        "title": {"$t": f"Capitulo {index}"},
        "published": {"$t": "2026-01-01T00:00:00Z"},
        "category": [{"term": category}],
        "link": [{"rel": "alternate", "href": f"https://datgarscanlation.blogspot.com/capitulo-{index}"}],
    }


def source_class():
    root = Path(__file__).parents[1]
    module = root.parent / "extensions-source-main" / "src" / "es" / "datgarscanlation"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_zeistmanga(module, build)
    assert config is not None
    bundle = _zeistmanga_bundle(
        (root / "engines" / "madara.py").read_text(encoding="utf-8"),
        (root / "engines" / "zeistmanga.py").read_text(encoding="utf-8"),
        config,
    )
    namespace = {"__name__": "test_datgarscanlation_bundle"}
    exec(compile(bundle, "datgarscanlation_es.py", "exec"), namespace)
    return namespace["SOURCE"]


class DatGarScanlationTest(unittest.IsolatedAsyncioTestCase):
    async def test_filters_new_feed_pagination_and_exact_reader(self):
        search_feed = {"feed": {"entry": [{
            **entry(1, "Series"), "title": {"$t": "Gato"},
        }]}}
        series_html = "<div id='latest'><script>const label = 'Gato';</script></div>"
        reader = '''<article id="reader"><img src="https://cdn/no.jpg"></article>
            <div class="check-box"><div class="separator"><img src="https://cdn/si.jpg"></div></div>'''
        fetcher = Fetcher([
            Response("https://datgarscanlation.blogspot.com/feeds/posts/default/-/Series/Ongoing/Manga/Action", search_feed),
            Response("https://datgarscanlation.blogspot.com/gato", series_html),
            Response("https://datgarscanlation.blogspot.com/feeds/posts/default/-/Gato", {
                "feed": {"openSearch$totalResults": {"$t": "151"}},
            }),
            Response("https://datgarscanlation.blogspot.com/feeds/posts/default/-/Gato", {"feed": {"entry": [entry(i) for i in range(1, 151)]}}),
            Response("https://datgarscanlation.blogspot.com/feeds/posts/default/-/Gato", {"feed": {"entry": [entry(151)]}}),
            Response("https://datgarscanlation.blogspot.com/capitulo-151", reader),
        ])
        source = source_class()(fetcher)

        filters = source.get_filters()
        search = await source.search("", 1, {"status": "Ongoing", "type": "Manga", "genres": ["Action"]})
        chapters = await source.chapters("https://datgarscanlation.blogspot.com/gato")
        pages = await source.pages(chapters[-1])

        self.assertNotIn("language", [item.id for item in filters])
        self.assertEqual(fetcher.requests[0][1], "https://datgarscanlation.blogspot.com/feeds/posts/default/-/Series/Ongoing/Manga/Action")
        self.assertEqual(search["items"][0].title, "Gato")
        self.assertEqual(fetcher.requests[2][2]["params"]["max-results"], 0)
        self.assertEqual(fetcher.requests[3][2]["params"]["max-results"], 150)
        self.assertEqual(fetcher.requests[4][2]["params"]["start-index"], 151)
        self.assertEqual(len(chapters), 151)
        self.assertEqual([page.source_id for page in pages], ["https://cdn/si.jpg"])
        self.assertEqual(source.capabilities.requests_per_minute, 120)
        self.assertEqual(source.capabilities.content_warning, "safe")


if __name__ == "__main__":
    unittest.main()
