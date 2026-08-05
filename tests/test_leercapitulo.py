from __future__ import annotations

import base64
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
    module = root.parent / "extensions-source-main" / "src" / "es" / "leercapitulo"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_generic(module, build)
    assert config is not None
    config["content_warning"] = _extract_kotlin_metadata(module)
    bundle = _generic_bundle(
        (root / "engines" / "madara.py").read_text(encoding="utf-8"),
        (root / "engines" / "generic.py").read_text(encoding="utf-8"),
        config,
    )
    namespace = {"__name__": "test_leercapitulo_bundle"}
    exec(compile(bundle, "leercapitulo_es.py", "exec"), namespace)
    return namespace["SOURCE"]


class LeerCapituloTest(unittest.IsolatedAsyncioTestCase):
    async def test_catalogs_search_filters_and_rate_limit(self):
        home = """
        <div class="hot-manga"><div class="thumbnails"><a href="/manga/gato/" title="Gato"><img data-src="/gato.jpg"></a></div></div>
        <div class="mainpage-manga"><img src="/perro.jpg"><div class="media-body"><a href="/manga/perro/"><h4>Perro</h4></a></div></div>
        """
        filtered = """
        <div class="cate-manga"><div class="mainpage-manga"><img src="/zorro.jpg"><div class="media-body"><a href="/manga/zorro/">Zorro</a></div></div></div>
        <ul class="pagination"><li class="active">1</li><li>2</li></ul>
        """
        autocomplete = [{"label": "Gato", "link": "/manga/gato/", "thumbnail": "/gato.jpg"}]
        fetcher = Fetcher([
            Response("https://www.leercapitulo.co", home),
            Response("https://www.leercapitulo.co", home),
            Response("https://www.leercapitulo.co/search-autocomplete?term=gato", autocomplete),
            Response("https://www.leercapitulo.co/genre/accion/?page=1", filtered),
        ])
        source = source_class()(fetcher)

        popular = await source.browse("popular")
        latest = await source.browse("latest")
        found = await source.search(" gato ")
        genre = await source.search("", 1, {"genre": "accion", "status": "completed"})

        self.assertEqual((popular["items"][0].title, latest["items"][0].title), ("Gato", "Perro"))
        self.assertEqual((found["items"][0].title, genre["items"][0].title, genre["has_more"]), ("Gato", "Zorro", True))
        self.assertEqual([item.id for item in source.get_filters()], ["genre", "alphabet", "status"])
        self.assertEqual(source.get_filters()[0].options[1], ("accion", "Acción"))
        self.assertEqual(source.requests_per_minute, 20)
        self.assertEqual(source.capabilities.content_warning, "safe")

    async def test_details_chapters_and_encrypted_pages(self):
        details = """
        <h1>Gato</h1><div class="cover-detail"><img data-lazy-src="/cover.jpg"></div>
        <div class="description-update"><span>Títulos Alternativos:</span> Cat
          <span>Estado:</span> Completed <a href="/genre/accion">Acción</a></div>
        <div id="example2">Sinopsis</div>
        <div class="chapter-list"><ul><li><a class="xanh" href="/leer/gato/2/">Capítulo 2</a></li></ul></div>
        """
        urls = ["https://cdn/1.jpg", "https://cdn/2.jpg"]
        source_key, encoded_key = source_class().decoder_keys
        raw = base64.b64encode(",".join(urls).encode()).decode()
        encoded = "".join(encoded_key[source_key.index(char)] if char in source_key else char for char in raw)
        reader = f'<meta property="ad:check" content="0-1"><p id="array_data">{encoded}</p>'
        fetcher = Fetcher([
            Response("https://www.leercapitulo.co/manga/gato/", details),
            Response("https://www.leercapitulo.co/manga/gato/", details),
            Response("https://www.leercapitulo.co/leer/gato/2/", reader),
        ])
        source = source_class()(fetcher)

        manga = await source.details("/manga/gato/")
        chapter = (await source.chapters(manga))[0]
        pages = await source.pages(chapter)

        self.assertEqual((manga.title, manga.status, manga.content_tags), ("Gato", "completed", ("Acción",)))
        self.assertIn("Alt name(s): Cat", manga.description)
        self.assertEqual((chapter.title, chapter.number), ("Capítulo 2", 2.0))
        self.assertEqual([page.source_id for page in pages], urls[::-1])


if __name__ == "__main__":
    unittest.main()
