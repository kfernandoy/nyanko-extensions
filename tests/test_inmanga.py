from __future__ import annotations

import json
import unittest
from pathlib import Path
from urllib.parse import parse_qs

from tools.generate import _extract_kotlin_metadata, _generic_bundle, _supported_generic


class Response:
    def __init__(self, url, text="", *, content=b"", headers=None):
        self.url, self.text, self.content = url, text, content
        self.status_code, self.headers = 200, headers or {}

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
    module = root.parent / "extensions-source-main" / "src" / "es" / "inmanga"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_generic(module, build)
    assert config is not None
    config["content_warning"] = _extract_kotlin_metadata(module)
    bundle = _generic_bundle(
        (root / "engines" / "madara.py").read_text(encoding="utf-8"),
        (root / "engines" / "generic.py").read_text(encoding="utf-8"),
        config,
    )
    namespace = {"__name__": "test_inmanga_bundle"}
    exec(compile(bundle, "inmanga_es.py", "exec"), namespace)
    return namespace["SOURCE"]


class InMangaTest(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_requests_and_pagination_match_kotlin(self):
        cards = "".join(
            f'<a href="/manga/gato-{index}"><h4 class="m0">Gato {index}</h4><img data-src="/gato-{index}.jpg"></a>'
            for index in range(10)
        )
        html = f"<body>{cards}</body>"
        fetcher = Fetcher([
            Response("https://inmanga.com/manga/getMangasConsultResult", html),
            Response("https://inmanga.com/manga/getMangasConsultResult", html),
            Response("https://inmanga.com/manga/getMangasConsultResult", html),
        ])
        source = source_class()(fetcher)

        popular = await source.browse("popular", 2)
        latest = await source.browse("latest", 3)
        search = await source.search("gato negro", 4)

        self.assertTrue(popular["has_more"] and latest["has_more"] and search["has_more"])
        self.assertEqual(popular["items"][0].source_id, "/manga/gato-0")
        bodies = [parse_qs(request[2]["content"], keep_blank_values=True) for request in fetcher.requests]
        self.assertEqual([body["filter[skip]"][0] for body in bodies], ["10", "20", "30"])
        self.assertEqual([body["filter[sortby]"][0] for body in bodies], ["1", "3", "1"])
        self.assertEqual(bodies[2]["filter[queryString]"][0], "gato negro")
        self.assertEqual(fetcher.requests[0][2]["headers"]["X-Requested-With"], "XMLHttpRequest")
        self.assertEqual(source.capabilities.content_warning, "safe")

    async def test_details_chapters_pages_and_cdn_match_kotlin(self):
        details = """
        <div class="col-md-3"><div class="panel widget"><img src="/cover.jpg">
          <a class="list-group-item">estado <span>En emisión</span></a></div></div>
        <div class="col-md-9"><h1>Gato</h1><div class="panel-body">Sinopsis</div></div>
        """
        chapter_data = json.dumps({"success": True, "result": [
            {"Number": 1, "RegistrationDate": "2026-08-03", "Identification": "c1", "FriendlyChapterNumber": "1"},
            {"Number": 2, "RegistrationDate": "2026-08-04T10:00:00", "Identification": "c2", "FriendlyChapterNumber": "2"},
        ]})
        pages = """
        <input id="ChapterIdentification" value="c2"><input id="MangaIdentification" value="m1">
        <img class="ImageContainer" id="1"><img class="ImageContainer" id="2">
        """
        fetcher = Fetcher([
            Response("https://inmanga.com/manga/gato", details),
            Response("https://inmanga.com/chapter/getall", json.dumps({"data": chapter_data})),
            Response("https://inmanga.com/chapter/chapterIndexControls?identification=c2", pages),
            Response("https://cdn1.intomanga.com/i/m/m1/c/c2/o/1.jpg", content=b"jpg", headers={"Content-Type": "image/jpeg"}),
        ])
        source = source_class()(fetcher)

        manga = await source.details("/manga/gato")
        chapters = await source.chapters(manga)
        page = (await source.pages(chapters[0]))[0]
        content = await source.page_bytes(page)

        self.assertEqual((manga.title, manga.status, manga.description), ("Gato", "ongoing", "Sinopsis"))
        self.assertEqual((chapters[0].number, chapters[0].uploaded_at), (2.0, "2026-08-04T00:00:00"))
        self.assertEqual(fetcher.requests[1][2]["params"]["mangaIdentification"], "gato")
        self.assertEqual(page.source_id, "https://cdn1.intomanga.com/i/m/m1/c/c2/o/1.jpg")
        self.assertEqual(b"".join(content.chunks), b"jpg")
        self.assertEqual(fetcher.requests[-1][2]["headers"]["Referer"], "https://inmanga.com/")


if __name__ == "__main__":
    unittest.main()
