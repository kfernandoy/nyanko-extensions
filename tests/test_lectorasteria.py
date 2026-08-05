from __future__ import annotations

import unittest
from pathlib import Path

from tools.generate import _moonlighttl_bundle, _supported_moonlighttl


class Response:
    def __init__(self, url, payload):
        self.url, self.status_code = url, 200
        self._payload = payload if isinstance(payload, dict) else None
        self.text = payload if isinstance(payload, str) else ""

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class Fetcher:
    def __init__(self, responses):
        self.responses, self.requests = responses, []

    async def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return self.responses.pop(0)


def source_class():
    root = Path(__file__).parents[1]
    module = root.parent / "extensions-source-main" / "src" / "es" / "lectorasteria"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_moonlighttl(module, build)
    assert config is not None
    bundle = _moonlighttl_bundle(
        (root / "engines" / "madara.py").read_text(encoding="utf-8"),
        (root / "engines" / "moonlighttl.py").read_text(encoding="utf-8"),
        config,
    )
    namespace = {"__name__": "test_lectorasteria_bundle"}
    exec(compile(bundle, "lectorasteria_es.py", "exec"), namespace)
    return namespace["SOURCE"]


def series(index: int) -> dict:
    return {
        "name": f"Gato {index:02}", "alternativeName": f"Cat {index:02}", "slug": f"gato-{index}",
        "urlImg": f"https://cdn/gato-{index}.jpg", "state_id": 1,
        "actualizacionCap": f"2026-08-{index + 1:02}", "created_at": "2026-01-01",
        "trending": {"visitas": index},
    }


class LectorAsteriaTest(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_and_cached_filtered_search_match_moonlight(self):
        top = {"response": {"diario": [[{"project": series(0)}]], "semanal": [], "mensual": []}}
        comics = {"response": [series(index) for index in range(16)]}
        fetcher = Fetcher([
            Response("https://lectorasteria.com/api/topSerie", top),
            Response("https://lectorasteria.com/api/comics", comics),
        ])
        source = source_class()(fetcher)

        popular = await source.browse("popular")
        first = await source.search("gato", 1, {"status": "1", "sort": "name", "ascending": True})
        second = await source.search("gato", 2, {"status": "1", "sort": "name", "ascending": True})

        self.assertEqual(popular["items"][0].title, "Gato 00")
        self.assertEqual((len(first["items"]), first["has_more"]), (15, True))
        self.assertEqual((len(second["items"]), second["has_more"]), (1, False))
        self.assertEqual(len(fetcher.requests), 2)
        self.assertEqual([item.id for item in source.get_filters()], ["sort", "ascending", "status"])
        self.assertEqual((source.profile, source.requests_per_minute), ("asteria", 120))
        self.assertEqual(source.capabilities.content_warning, "mixed")

    async def test_details_chapters_and_asteria_page_selector(self):
        project = {"response": series(0) | {
            "sinopsis": "Sinopsis", "alternativeName": "Gato alternativo", "state_id": 4,
            "genders": [{"gender": {"name": "Acción"}}],
            "autors": [{"autor": {"name": "Ana"}}], "artists": [{"artist": {"name": "Beto"}}],
            "lastChapters": [{"slug": "capitulo-2", "num": 2, "name": "Final", "created_at": "2026-08-04T00:00:00.000Z"}],
        }}
        pages = """
        <main><img class="block" src="/wrong.jpg"><div><img class="block" src="/pages/1.jpg"></div></main>
        """
        fetcher = Fetcher([
            Response("https://lectorasteria.com/api/showProject/gato-0", project),
            Response("https://lectorasteria.com/api/showProject/gato-0", project),
            Response("https://lectorasteria.com/ver/gato-0/capitulo-2", pages),
        ])
        source = source_class()(fetcher)

        manga = await source.details("https://lectorasteria.com/ver/gato-0")
        chapter = (await source.chapters(manga))[0]
        page_list = await source.pages(chapter)

        self.assertEqual((manga.author, manga.artist, manga.status), ("Ana", "Beto", "completed"))
        self.assertEqual(manga.content_tags, ("Acción",))
        self.assertIn("Nombres alternativos: Gato alternativo", manga.description)
        self.assertEqual((chapter.number, chapter.language), (2.0, "es"))
        self.assertEqual([page.source_id for page in page_list], ["https://lectorasteria.com/pages/1.jpg"])


if __name__ == "__main__":
    unittest.main()
