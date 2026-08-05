from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.generate import _extract_kotlin_metadata, _generic_bundle, _supported_generic


class Response:
    def __init__(self, url, payload):
        self.url = url
        self.text = payload if isinstance(payload, str) else json.dumps(payload)
        self.status_code = 200

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
    module = root.parent / "extensions-source-main" / "src" / "es" / "koinoboriscan"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_generic(module, build)
    assert config is not None
    config["content_warning"] = _extract_kotlin_metadata(module)
    bundle = _generic_bundle(
        (root / "engines" / "madara.py").read_text(encoding="utf-8"),
        (root / "engines" / "generic.py").read_text(encoding="utf-8"),
        config,
    )
    namespace = {"__name__": "test_koinoboriscan_bundle"}
    exec(compile(bundle, "koinoboriscan_es.py", "exec"), namespace)
    return namespace["SOURCE"]


def series(slug: str, title: str) -> dict:
    return {
        "series_slug": slug, "title": title, "description": None,
        "thumbnail": f"https://cdn/{slug}.jpg", "status": "Ongoing", "author": None, "tags": [],
    }


class KoinoboriScanTest(unittest.IsolatedAsyncioTestCase):
    async def test_api_catalogs_and_cached_search_match_kotlin(self):
        top = {
            "mensualRes": [series("gato", "Gato")],
            "weekRes": [series("gato", "Gato"), series("perro", "Perro")],
            "dayRes": [],
        }
        latest = [series("zorro", "Zorro")]
        library = [series(f"gato-{index}", f"Gato {index}") for index in range(25)]
        fetcher = Fetcher([
            Response("https://api.visorkoi.com/api/topSeries", top),
            Response("https://api.visorkoi.com/api/lastupdates", latest),
            Response("https://api.visorkoi.com/api/allComics", library),
        ])
        source = source_class()(fetcher)

        popular = await source.browse("popular")
        recent = await source.browse("latest")
        first = await source.search("gato", 1)
        second = await source.search("gato", 2)

        self.assertEqual([item.source_id for item in popular["items"]], ["gato", "perro"])
        self.assertEqual(recent["items"][0].title, "Zorro")
        self.assertEqual((len(first["items"]), first["has_more"]), (24, True))
        self.assertEqual((len(second["items"]), second["has_more"]), (1, False))
        self.assertEqual(len(fetcher.requests), 3)
        self.assertEqual(source.requests_per_minute, 120)
        self.assertEqual(source.capabilities.content_warning, "nsfw")

    async def test_next_payload_details_chapters_and_pages_match_kotlin(self):
        payload = series("gato", " Gato ") | {
            "description": " Sinopsis ", "status": "Completado", "author": " Ana ",
            "tags": [{"name": " Acción "}],
            "Season": [{"Chapter": [{
                "chapter_slug": "capitulo-2", "chapter_name": "Capítulo 2",
                "chapter_title": "Final", "created_at": "2026-08-04T10:11:12.123Z",
            }]}],
        }
        escaped = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).replace('"', '\\"')
        html = f'<script>self.__next_f.push("info\\":{escaped},\\"userIsFollowed")</script>'
        pages = '<div class="relative"><img src="/pages/1.jpg"></div>'
        fetcher = Fetcher([
            Response("https://visorkoi.com/comic/gato", html),
            Response("https://visorkoi.com/comic/gato", html),
            Response("https://visorkoi.com/comic/gato/capitulo-2", pages),
        ])
        source = source_class()(fetcher)

        manga = await source.details("gato")
        chapter = (await source.chapters(manga))[0]
        page = (await source.pages(chapter))[0]

        self.assertEqual((manga.title, manga.author, manga.status), ("Gato", "Ana", "completed"))
        self.assertEqual((manga.description, manga.content_tags), ("Sinopsis", ("Acción",)))
        self.assertEqual(chapter.title, "Capítulo 2: Final")
        self.assertEqual(chapter.uploaded_at, "2026-08-04T10:11:12.123000+00:00")
        self.assertEqual(page.source_id, "https://visorkoi.com/pages/1.jpg")


if __name__ == "__main__":
    unittest.main()
