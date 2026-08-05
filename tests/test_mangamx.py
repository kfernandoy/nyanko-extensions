from __future__ import annotations

import base64
import json
import unittest
from pathlib import Path

from tools.generate import _generic_bundle, _supported_generic


class Response:
    def __init__(self, url, text):
        self.url, self.text, self.status_code = url, text, 200

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
    module = root.parent / "extensions-source-main" / "src" / "es" / "mangamx"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_generic(module, build)
    assert config is not None
    config["content_warning"] = "mixed"
    bundle = _generic_bundle(
        (root / "engines" / "madara.py").read_text(encoding="utf-8"),
        (root / "engines" / "generic.py").read_text(encoding="utf-8"),
        config,
    )
    namespace = {"__name__": "test_mangamx_bundle"}
    exec(compile(bundle, "mangamx_es.py", "exec"), namespace)
    return namespace["SOURCE"]


class MangaOniTest(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_details_chapters_and_encoded_pages(self):
        catalog = '<div id="article-div"><a href="/obra/gato"><img data-src="/gato.jpg"><div>X</div><div>Gato</div></a></div>'
        details = '''<h1>Gato</h1><img src="/img/cover-gato.jpg"><div id="sinopsis">Sinopsis gato</div>
        <div id="info-i">Autor: Ana Fecha: 2024</div><div id="categ"><a>Acción</a></div><div><strong>Estado:</strong><span>En desarrollo</span></div>'''
        chapters = '<div id="c_list"><a href="/capitulo/2">Capítulo 2 <span data-num="2" datetime="2026-08-05 12:30:00"></span></a></div>'
        decoded = 'https://cdn.example/' + '||' + json.dumps(["1.jpg", "2.jpg"])
        encoded = base64.b64encode(decoded.encode()).decode()
        pages = f"<script>var unicap = '{encoded}';</script>"
        fetcher = Fetcher([
            Response("https://manga-oni.com/directorio", catalog),
            Response("https://manga-oni.com/obra/gato", details),
            Response("https://manga-oni.com/obra/gato", chapters),
            Response("https://manga-oni.com/capitulo/2", pages),
        ])
        source = source_class()(fetcher)

        result = await source.search("", 3, {"genre": "43", "adult": "0", "ascending": True})
        manga = result["items"][0]
        detail = await source.details(manga)
        chapter = (await source.chapters(manga))[0]
        images = await source.pages(chapter)

        self.assertEqual((manga.title, manga.cover_url), ("Gato", "https://manga-oni.com/gato.jpg"))
        self.assertEqual(fetcher.requests[0][2]["params"]["genero"], "43")
        self.assertEqual(fetcher.requests[0][2]["params"]["orden"], "asc")
        self.assertEqual((detail.author, detail.artist, detail.status), ("Ana", "Ana", "ongoing"))
        self.assertEqual((chapter.number, chapter.uploaded_at), (2.0, "2026-08-05T12:30:00"))
        self.assertEqual([page.source_id for page in images], ["https://cdn.example/1.jpg", "https://cdn.example/2.jpg"])
        self.assertEqual(source.get_filters()[4].options[-1], ("43", "Isekai"))
        self.assertEqual(source.capabilities.content_warning, "mixed")


if __name__ == "__main__":
    unittest.main()
