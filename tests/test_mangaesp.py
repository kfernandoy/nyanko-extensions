from __future__ import annotations

import unittest
from pathlib import Path

from tools.generate import _mangathemesia_bundle, _supported_mangathemesia


class Response:
    def __init__(self, url, text):
        self.url, self.text, self.status_code = url, text, 200

    def raise_for_status(self):
        pass


class Fetcher:
    def __init__(self, response):
        self.response, self.requests = response, []

    async def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return self.response


def source_class():
    root = Path(__file__).parents[1]
    module = root.parent / "extensions-source-main" / "src" / "es" / "mangaesp"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_mangathemesia(module, build)
    assert config is not None
    bundle = _mangathemesia_bundle(
        (root / "engines" / "base.py").read_text(encoding="utf-8"),
        (root / "engines" / "mangathemesia.py").read_text(encoding="utf-8"),
        config,
    )
    namespace = {"__name__": "test_mangaesp_bundle"}
    exec(compile(bundle, "mangaesp_es.py", "exec"), namespace)
    return namespace["SOURCE"]


class MangaEspTest(unittest.IsolatedAsyncioTestCase):
    async def test_projects_filter_and_metadata(self):
        html = '<div class="bsx"><a href="/manga/gato/" title="Gato"><img src="/gato.jpg"></a></div>'
        fetcher = Fetcher(Response("https://mangaesp.topmanhuas.org/proyectos/", html))
        source = source_class()(fetcher)

        manga = (await source.search("gato", 2, {"projects": True}))[0]

        self.assertEqual((manga.title, manga.source_id), ("Gato", "https://mangaesp.topmanhuas.org/manga/gato/"))
        self.assertEqual(fetcher.requests[0][1:], (
            "https://mangaesp.topmanhuas.org/proyectos/",
            {"params": {"title": "gato", "page": "2"}},
        ))
        self.assertEqual(source.get_filters()[0].id, "projects")
        self.assertEqual((source.requests_per_minute, source.project_directory), (180, "/proyectos"))
        self.assertEqual((source.date_format, source.date_locale), ("MMMM dd, yyyy", "en"))
        self.assertEqual(source.capabilities.content_warning, "safe")


if __name__ == "__main__":
    unittest.main()
