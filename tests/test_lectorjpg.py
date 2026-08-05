from __future__ import annotations

import base64
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
    module = root.parent / "extensions-source-main" / "src" / "es" / "lectorjpg"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_generic(module, build)
    assert config is not None
    config["content_warning"] = _extract_kotlin_metadata(module)
    bundle = _generic_bundle(
        (root / "engines" / "madara.py").read_text(encoding="utf-8"),
        (root / "engines" / "generic.py").read_text(encoding="utf-8"),
        config,
    )
    namespace = {"__name__": "test_lectorjpg_bundle"}
    exec(compile(bundle, "lectorjpg_es.py", "exec"), namespace)
    return namespace["SOURCE"]


def result(slug: str, cursor=None) -> dict:
    return {"data": [{"name": slug.title(), "slug": slug, "cover_url": f"https://cdn/{slug}.jpg"}], "next_cursor": cursor}


class LectorJpgTest(unittest.IsolatedAsyncioTestCase):
    async def test_api_cursors_search_and_generated_genres(self):
        fetcher = Fetcher([
            Response("https://api.visorjpg.lat/home/trending", result("gato")),
            Response("https://api.visorjpg.lat/home/lastest-updates", result("perro", "latest-2")),
            Response("https://api.visorjpg.lat/home/lastest-updates", result("zorro")),
            Response("https://api.visorjpg.lat/search", result("lobo", "search-2")),
            Response("https://api.visorjpg.lat/search", result("tigre")),
        ])
        source = source_class()(fetcher)

        popular = await source.browse("popular")
        latest1 = await source.browse("latest", 1)
        latest2 = await source.browse("latest", 2)
        search1 = await source.search("amor", 1, {"genres": ["romance", "drama"]})
        search2 = await source.search("amor", 2, {"genres": ["romance", "drama"]})

        first_cursor = fetcher.requests[1][2]["params"]["cursor"]
        decoded = json.loads(base64.b64decode(first_cursor))
        self.assertEqual((decoded["id"], decoded["_pointsToNextItems"]), (0, True))
        self.assertEqual(fetcher.requests[2][2]["params"]["cursor"], "latest-2")
        self.assertEqual(fetcher.requests[4][2]["params"], {
            "cursor": "search-2", "name": "amor", "genres": "romance,drama",
        })
        self.assertEqual((popular["items"][0].source_id, latest1["has_more"], latest2["has_more"]), ("gato", True, False))
        self.assertEqual((search1["has_more"], search2["has_more"]), (True, False))
        self.assertEqual(source.get_filters()[0].options[0], ("bdsm", "BDSM"))
        self.assertEqual((source.requests_per_minute, source.capabilities.content_warning), (180, "mixed"))

    async def test_details_chapters_and_svelte_pages(self):
        details = """
        <div class="grid"><h1>Gato</h1><div class="container"><p>Sinopsis</p></div></div>
        <div class="bg_main bg-cover" style="background-image: url(&quot;https://cdn/cover.jpg&quot;)"></div>
        <div class="grid"><div class="flex"><span>Status</span></div><div><span>end</span></div></div>
        <a href="/series?genres=romance"><span>Romance</span></a>
        """
        chapters = """
        <div class="grid"><a class="group" href="/series/gato/capitulo-2"><span class="truncate">Capítulo 2</span>
          <span class="w-fit">04/08/2026</span></a></div>
        """
        pages = '<script>window.svelteKit={images:["https://cdn/1.jpg","https://cdn/2.jpg"]}</script>'
        fetcher = Fetcher([
            Response("https://visorjpg.lat/series/gato", details),
            Response("https://visorjpg.lat/series/gato", chapters),
            Response("https://visorjpg.lat/series/gato/capitulo-2", pages),
        ])
        source = source_class()(fetcher)

        manga = await source.details("gato")
        chapter = (await source.chapters(manga))[0]
        page_list = await source.pages(chapter)

        self.assertEqual((manga.status, manga.content_tags), ("completed", ("Romance",)))
        self.assertEqual((manga.description, manga.cover_url), ("Sinopsis", "https://cdn/cover.jpg"))
        self.assertEqual((chapter.number, chapter.uploaded_at), (2.0, "2026-08-04T00:00:00"))
        self.assertEqual([page.source_id for page in page_list], ["https://cdn/1.jpg", "https://cdn/2.jpg"])


if __name__ == "__main__":
    unittest.main()
