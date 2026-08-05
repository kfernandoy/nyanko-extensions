from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.generate import _extract_kotlin_metadata, _generic_bundle, _supported_generic


class Response:
    def __init__(self, url: str, payload):
        self.url, self.status_code = url, 200
        self.text = payload if isinstance(payload, str) else json.dumps(payload)

    def raise_for_status(self):
        pass

    def json(self):
        return json.loads(self.text)


class Fetcher:
    def __init__(self, responses):
        self.responses, self.requests = responses, []

    async def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return self.responses.pop(0)


def source_class():
    root = Path(__file__).parents[1]
    module = root.parent / "extensions-source-main" / "src" / "es" / "leermangaesp"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_generic(module, build)
    assert config is not None
    config["content_warning"] = _extract_kotlin_metadata(module)
    bundle = _generic_bundle(
        (root / "engines" / "madara.py").read_text(encoding="utf-8"),
        (root / "engines" / "generic.py").read_text(encoding="utf-8"),
        config,
    )
    namespace = {"__name__": "test_leermangaesp_bundle"}
    exec(compile(bundle, "leermangaesp_es.py", "exec"), namespace)
    return namespace["SOURCE"]


def manga(slug: str, title: str, date: str = "") -> dict:
    return {"slug": slug, "titulo": title, "portada": f"/{slug}.jpg", "fecha_publicacion": date}


DETAILS = """
<h1 class="manga-title">Gato</h1><img class="manga-cover" src="/cover.jpg">
<div id="synopsis-text">Sinopsis</div><div id="info-block"><span class="info-value">En curso</span></div>
<div class="info-generos"><span class="genero-item">Acción</span></div>
"""


class LeerMangaEspTest(unittest.IsolatedAsyncioTestCase):
    async def test_json_catalogs_search_filters_and_deeplink(self):
        popular = f'<script id="ssr-trends-data">{json.dumps([manga("gato", "Gato")])}</script>'
        latest = [manga("viejo", "Viejo", "2026-01-01"), manga("nuevo", "Nuevo", "2026-08-05")]
        search = {"resultados": [manga("gato", "Gato")], "page": 1, "total_pages": 2}
        fetcher = Fetcher([
            Response("https://mangalect.org", popular),
            Response("https://mangalect.org/api/latest_chapters_with_dates", latest),
            Response("https://mangalect.org/api/buscar_mangas", search),
            Response("https://mangalect.org/info/gato/", DETAILS),
        ])
        source = source_class()(fetcher)

        top = await source.browse("popular")
        recent = await source.browse("latest")
        found = await source.search("gato", 1, {"type": "Manga", "genres": ["Acción"]})
        direct = await source.search("https://mangalect.org/manga/gato")

        self.assertEqual((top["items"][0].title, recent["items"][0].title), ("Gato", "Nuevo"))
        self.assertEqual((found["items"][0].title, found["has_more"], direct["items"][0].title), ("Gato", True, "Gato"))
        self.assertEqual(fetcher.requests[2][2]["params"], {
            "page": "1", "page_size": "20", "query": "gato", "tipo": "Manga", "generos": "Acción",
        })
        self.assertEqual([item.id for item in source.get_filters()], ["type", "genres"])
        self.assertEqual(source.capabilities.content_warning, "mixed")

    async def test_details_paginated_chapters_and_pages(self):
        first = DETAILS + """
        <div id="chapter-list"><a class="chapter-link" data-chapter="2" href="/leer-m/gato/2/">
          <span class="chapter-title">Capítulo 2</span><span class="chapter-date">August 5, 2026</span></a></div>
        <a id="more-link" href="?page=2">Más</a>
        """
        second = """
        <div id="chapter-list">
          <a class="chapter-link" data-chapter="2" href="/leer-m/gato/2/">Capítulo 2</a>
          <a class="chapter-link" data-chapter="1" href="/leer-m/gato/1/"><span class="chapter-title">Capítulo 1</span></a>
          <a id="continue-link" class="chapter-link" data-chapter="0" href="/continue">Continuar</a>
        </div>
        """
        reader = '<div id="cascade-view"><img class="manga-image" src="/pages/1.jpg"><img src="/ad.jpg"></div>'
        fetcher = Fetcher([
            Response("https://mangalect.org/info/gato/", DETAILS),
            Response("https://mangalect.org/info/gato/", first),
            Response("https://mangalect.org/info/gato/?page=2", second),
            Response("https://mangalect.org/leer-m/gato/2/", reader),
        ])
        source = source_class()(fetcher)

        details = await source.details("gato")
        chapters = await source.chapters(details)
        pages = await source.pages(chapters[0])

        self.assertEqual((details.status, details.content_tags), ("ongoing", ("Acción",)))
        self.assertEqual([(chapter.title, chapter.number) for chapter in chapters], [("Capítulo 2", 2.0), ("Capítulo 1", 1.0)])
        self.assertEqual(chapters[0].uploaded_at, "2026-08-05T00:00:00")
        self.assertEqual([page.source_id for page in pages], ["https://mangalect.org/pages/1.jpg"])


if __name__ == "__main__":
    unittest.main()
