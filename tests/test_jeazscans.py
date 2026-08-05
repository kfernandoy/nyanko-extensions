from __future__ import annotations

import base64
import json
import unittest
from pathlib import Path

from tools.generate import _extract_kotlin_metadata, _generic_bundle, _supported_generic


class Response:
    def __init__(self, url, text=""):
        self.url, self.text, self.status_code = url, text, 200

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
    module = root.parent / "extensions-source-main" / "src" / "es" / "jeazscans"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_generic(module, build)
    assert config is not None
    config["content_warning"] = _extract_kotlin_metadata(module)
    bundle = _generic_bundle(
        (root / "engines" / "madara.py").read_text(encoding="utf-8"),
        (root / "engines" / "generic.py").read_text(encoding="utf-8"),
        config,
    )
    namespace = {"__name__": "test_jeazscans_bundle"}
    exec(compile(bundle, "jeazscans_es.py", "exec"), namespace)
    return namespace["SOURCE"]


class JeazScansTest(unittest.IsolatedAsyncioTestCase):
    async def test_home_sections_and_json_search_match_kotlin(self):
        home = """
        <section><h3>Top Rankings</h3><a href="/manga.php?id=1"><h4>Gato</h4><img src="/gato.jpg"></a></section>
        <section><h3>Lanzamientos</h3><div class="manga-card"><a href="/manga.php?id=2"><img src="/perro.jpg"></a><figcaption>Perro</figcaption></div></section>
        """
        search = json.dumps([
            {"id": -1, "titulo": "", "portada": None},
            {"id": 3, "titulo": "Zorro", "portada": "covers/zorro.jpg"},
        ])
        fetcher = Fetcher([
            Response("https://lectorhub.j5z.xyz/", home),
            Response("https://lectorhub.j5z.xyz/", home),
            Response("https://lectorhub.j5z.xyz/ajax_search.php?q=zorro", search),
        ])
        source = source_class()(fetcher)

        popular = await source.browse("popular")
        latest = await source.browse("latest")
        found = await source.search(" zorro ")

        self.assertEqual(popular["items"][0].source_id, "/manga.php?id=1")
        self.assertEqual(latest["items"][0].title, "Perro")
        self.assertEqual(found["items"][0].cover_url, "https://lectorhub.j5z.xyz/covers/zorro.jpg")
        self.assertEqual(fetcher.requests[-1][2]["params"], {"q": "zorro"})
        self.assertEqual(source.requests_per_minute, 120)
        self.assertEqual(source.capabilities.content_warning, "safe")

    async def test_details_chapters_and_both_page_paths_match_kotlin(self):
        details = """
        <h1 class="blood-title">Gato</h1><div class="lg:col-span-3"><div class="cultivation-panel"><img src="/cover.jpg"></div></div>
        <div class="text-gray-200"><h3>SINOPSIS</h3>Sinopsis</div><span class="status-badge">En cultivo</span>
        <a href="directorio.php?genero=accion">Acción</a>
        """
        chapters = """
        <div id="chaptersContainer"><a class="chapter-item" data-chapter-number="2" href="/leer/gato/capitulo-2">
          <span class="chapter-title">Capítulo especial</span><span><i class="ph-clock"></i>04 Aug, 2026</span></a></div>
        """
        direct_url = "https://cdn.example/1.jpg"
        encoded = base64.b64encode(direct_url[::-1].encode()).decode()
        direct_pages = f'<div class="page-container"><img class="protected-img" data-verify="{encoded}"></div>'
        api_page_url = "https://cdn.example/2.jpg"
        api_encoded = base64.b64encode(api_page_url[::-1].encode()).decode()
        api_payload = json.dumps({"success": True, "paginas": [
            {"orden": 2, "data_verify": api_encoded}, {"orden": 1, "data_verify": api_encoded},
        ]})
        fetcher = Fetcher([
            Response("https://lectorhub.j5z.xyz/manga.php?id=1", details),
            Response("https://lectorhub.j5z.xyz/manga.php?id=1", chapters),
            Response("https://lectorhub.j5z.xyz/leer/gato/capitulo-2", direct_pages),
            Response("https://lectorhub.j5z.xyz/leer/gato/capitulo-2", "<html></html>"),
            Response("https://lectorhub.j5z.xyz/api_lector.php?slug=gato&cap=2", api_payload),
        ])
        source = source_class()(fetcher)

        manga = await source.details("/manga.php?id=1")
        chapter = (await source.chapters(manga))[0]
        direct = await source.pages(chapter)
        fallback = await source.pages(chapter)

        self.assertEqual((manga.status, manga.content_tags), ("ongoing", ("Acción",)))
        self.assertEqual((chapter.number, chapter.uploaded_at), (2.0, "2026-08-04T00:00:00"))
        self.assertEqual(direct[0].source_id, direct_url)
        self.assertEqual([page.source_id for page in fallback], [api_page_url])
        self.assertEqual(fetcher.requests[-1][2]["headers"]["Referer"], (
            "https://lectorhub.j5z.xyz/leer/gato/capitulo-2"
        ))


if __name__ == "__main__":
    unittest.main()
