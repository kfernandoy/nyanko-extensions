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


def source_class():
    root = Path(__file__).parents[1]
    module = root.parent / "extensions-source-main" / "src" / "es" / "darkroomfansub"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_zeistmanga(module, build)
    assert config is not None
    bundle = _zeistmanga_bundle(
        (root / "engines" / "madara.py").read_text(encoding="utf-8"),
        (root / "engines" / "zeistmanga.py").read_text(encoding="utf-8"),
        config,
    )
    namespace = {"__name__": "test_darkroomfansub_bundle"}
    exec(compile(bundle, "darkroomfansub_es.py", "exec"), namespace)
    return namespace["SOURCE"]


class DarkRoomFansubTest(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_latest_popular_feed_rate_and_exact_reader(self):
        feed = {"feed": {"entry": [{
            "title": {"$t": "Gato"}, "category": [{"term": "Series"}],
            "link": [{"rel": "alternate", "href": "https://lector-darkroomfansub.blogspot.com/gato"}],
        }]}}
        reader = '''<article id="reader">
            <img src="https://cdn/no-es-pagina.jpg">
            <div class="separator"><img src="https://cdn/pagina.jpg"></div>
        </article>'''
        fetcher = Fetcher([
            Response("https://lector-darkroomfansub.blogspot.com/feeds/posts/default/-/Series", feed),
            Response("https://lector-darkroomfansub.blogspot.com/capitulo", reader),
        ])
        source = source_class()(fetcher)

        latest = await source.browse("latest")
        popular = await source.browse("popular")
        pages = await source.pages("https://lector-darkroomfansub.blogspot.com/capitulo")

        self.assertEqual(latest, [])
        self.assertEqual(popular[0].title, "Gato")
        self.assertEqual(fetcher.requests[0][2]["params"]["orderby"], "published")
        self.assertEqual(source.capabilities.requests_per_minute, 180)
        self.assertEqual([page.source_id for page in pages], ["https://cdn/pagina.jpg"])


if __name__ == "__main__":
    unittest.main()
