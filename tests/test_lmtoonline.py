from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.generate import _extract_kotlin_metadata, _generic_bundle, _supported_generic


class Response:
    def __init__(self, url: str, payload):
        self.url, self.status_code = url, 200
        if isinstance(payload, str):
            self.text = payload
        else:
            data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            stream = json.dumps([1, f'0:{{"payload":{data}}}\n'])
            self.text = f"<script>self.__next_f.push({stream})</script>"

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
    module = root.parent / "extensions-source-main" / "src" / "es" / "lmtoonline"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_generic(module, build)
    assert config is not None
    config["content_warning"] = _extract_kotlin_metadata(module)
    bundle = _generic_bundle(
        (root / "engines" / "madara.py").read_text(encoding="utf-8"),
        (root / "engines" / "generic.py").read_text(encoding="utf-8"),
        config,
    )
    namespace = {"__name__": "test_lmtoonline_bundle"}
    exec(compile(bundle, "lmtoonline_es.py", "exec"), namespace)
    return namespace["SOURCE"]


def manga(index: int) -> dict:
    return {
        "slug": f"gato-{index}", "title": f"Gato {index:02}", "alternativeTitles": [f"Cat {index:02}"],
        "coverImage": f"https://cdn/gato-{index}.jpg", "isAdult": False, "type": "manga",
        "status": "ongoing", "demographic": "shounen", "genres": ["Acción"],
        "latestChapterCreatedAt": f"2026-08-{index + 1:02}T00:00:00.000Z", "totalViews": index,
    }


class LmtosTest(unittest.IsolatedAsyncioTestCase):
    async def test_popular_cached_filtered_search_and_latest(self):
        popular = '<section><a class="group" href="/manga/destacado/"><img src="/cover.jpg"><div><h3>Destacado</h3></div></a></section>'
        catalog = {"mangas": [manga(index) for index in range(21)]}
        fetcher = Fetcher([
            Response("https://lmtos.net/destacados", popular),
            Response("https://lmtos.net/series", catalog),
        ])
        source = source_class()(fetcher)
        filters = {
            "genres": ["Acción"], "status": "ongoing", "demographic": "shounen",
            "type": "manga", "nsfw": "hide", "order": "a-z",
        }

        top = await source.browse("popular")
        first = await source.search("cat", 1, filters)
        second = await source.search("cat", 2, filters)
        latest = await source.browse("latest")

        self.assertEqual(top["items"][0].title, "Destacado")
        self.assertEqual((len(first["items"]), first["has_more"]), (20, True))
        self.assertEqual((len(second["items"]), second["has_more"]), (1, False))
        self.assertEqual(latest["items"][0].title, "Gato 20")
        self.assertEqual(len(fetcher.requests), 2)
        self.assertEqual([item.id for item in source.get_filters()], ["genres", "status", "demographic", "type", "nsfw", "order"])
        self.assertEqual(source.get_filters()[1].options[-1], ("paused", "Pausado"))
        self.assertEqual(source.get_filters()[-1].options[-1], ("views", "Mejor valorados"))
        self.assertEqual((source.requests_per_minute, source.capabilities.content_warning), (180, "mixed"))

    async def test_details_chapters_and_pages(self):
        item = manga(0) | {"description": "Sinopsis", "author": "Ana", "artist": "Beto", "alternativeTitles": ["Cat"]}
        chapters = {"manga": item, "chapters": [
            {"slug": "capitulo-2", "number": 2.0, "createdAt": "2026-08-05T10:11:12.123Z"},
        ]}
        pages = {"chapter": {"pages": ["https://cdn/pages/1.jpg", "https://cdn/pages/2.jpg"]}}
        fetcher = Fetcher([
            Response("https://lmtos.net/manga/gato-0", {"manga": item}),
            Response("https://lmtos.net/manga/gato-0", chapters),
            Response("https://lmtos.net/manga/gato-0/capitulo-2", pages),
        ])
        source = source_class()(fetcher)

        details = await source.details("gato-0")
        chapter = (await source.chapters(details))[0]
        page_list = await source.pages(chapter)

        self.assertEqual((details.author, details.artist, details.status), ("Ana", "Beto", "ongoing"))
        self.assertEqual(details.content_tags, ("Manga", "Acción"))
        self.assertIn("Nombres alternativos: Cat", details.description)
        self.assertEqual((chapter.title, chapter.number, chapter.uploaded_at), ("Cap. 2", 2.0, "2026-08-05T10:11:12.123000+00:00"))
        self.assertEqual([page.source_id for page in page_list], ["https://cdn/pages/1.jpg", "https://cdn/pages/2.jpg"])


if __name__ == "__main__":
    unittest.main()
