from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.generate import _extract_kotlin_metadata, _heavenmanga_bundle, _supported_heavenmanga


class Response:
    def __init__(self, url, payload):
        self.url, self.status_code = url, 200
        self.text = payload if isinstance(payload, str) else json.dumps(payload)

    def json(self):
        return json.loads(self.text)

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
    module = root.parent / "extensions-source-main" / "src" / "es" / "heavenmanga"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_heavenmanga(module, build)
    assert config is not None
    config["content_warning"] = _extract_kotlin_metadata(module)
    bundle = _heavenmanga_bundle(
        (root / "engines" / "madara.py").read_text(encoding="utf-8"),
        (root / "engines" / "heavenmanga.py").read_text(encoding="utf-8"),
        config,
    )
    namespace = {"__name__": "test_heavenmanga_bundle"}
    exec(compile(bundle, "heavenmanga_es.py", "exec"), namespace)
    return namespace["SOURCE"]


def card(title="Gato", url="/manga/gato/", cover="/gato.jpg"):
    return f'<div class="page-item-detail"><div class="manga-name">{title}</div><a href="{url}"><img src="{cover}"></a></div>'


class HeavenMangaTest(unittest.IsolatedAsyncioTestCase):
    async def test_popular_and_latest_have_distinct_kotlin_flows(self):
        popular_html = card() + '<ul class="pagination"><li><a rel="next">Siguiente</a></li></ul>'
        latest_html = """
        <div class="col-lg-8"><div id="loop-content">
          <div class="list-group-item"><a href="/manga/gato/"><span class="captitle">Gato</span></a></div>
          <div class="list-group-item"><div>Novela</div><a href="/manga/novela/">Novela</a></div>
          <div class="list-group-item"><a href="/manga/gato/">Duplicado</a></div>
        </div></div>
        """
        fetcher = Fetcher([
            Response("https://heavenmanga.com/top", popular_html),
            Response("https://heavenmanga.com", latest_html),
        ])
        source = source_class()(fetcher)

        popular = await source.browse("popular", 2)
        latest = await source.browse("latest", 1)

        self.assertEqual(fetcher.requests[0][2]["params"], {"orderby": "views", "page": "2"})
        self.assertNotIn("params", fetcher.requests[1][2])
        self.assertTrue(popular["has_more"])
        self.assertEqual([item.title for item in latest["items"]], ["Gato"])
        self.assertEqual(
            latest["items"][0].cover_url,
            "https://heavenmanga.com/uploads/manga/gato/cover/cover_250x350.jpg",
        )
        self.assertEqual(source.capabilities.content_warning, "mixed")

    async def test_text_search_and_each_filter_build_the_exact_routes(self):
        text_html = """
        <div class="c-tabs-item__content"><h4><a href="/manga/gato/">Gato</a></h4>
          <img data-src="/search.jpg"></div>
        """
        fetcher = Fetcher([
            Response("https://heavenmanga.com/buscar", text_html),
            Response("https://heavenmanga.com/genero/accion.html", card()),
            Response("https://heavenmanga.com/letra/manga.html", card()),
            Response("https://heavenmanga.com/adulto", card()),
        ])
        source = source_class()(fetcher)

        with self.assertRaisesRegex(ValueError, "3 caracteres"):
            await source.search("ab")
        text_result = await source.search(" gato ", 2)
        await source.search("", 1, {"genre": "accion"})
        await source.search("", 1, {"alphabet": "g"})
        await source.search("", 1, {"list": "adulto"})

        self.assertEqual(fetcher.requests[0][1:3], (
            "https://heavenmanga.com/buscar", {"params": {"query": "gato", "page": "2"}},
        ))
        self.assertEqual(fetcher.requests[1][1], "https://heavenmanga.com/genero/accion.html")
        self.assertEqual(fetcher.requests[2][2]["params"], {"alpha": "g"})
        self.assertEqual(fetcher.requests[3][1], "https://heavenmanga.com/adulto")
        self.assertEqual(text_result["items"][0].cover_url, "https://heavenmanga.com/search.jpg")
        filters = source.get_filters()
        self.assertEqual([item.id for item in filters], ["genre", "alphabet", "list"])
        self.assertIn(("Matrimonio", "matrimonio"), filters[0].options)
        self.assertIn(("0-9", "0-9"), filters[1].options)
        self.assertEqual(filters[2].options[-1], ("Lista Adulto", "adulto"))

    async def test_details_json_chapters_and_script_pages_match_kotlin(self):
        details = """
        <div class="tab-summary"><div class="genres-content"><a>Acción</a><a>Drama</a></div>
          <div class="summary_image"><img data-src="/cover.jpg"></div></div>
        <div class="description-summary"><p>Uno</p><p>Dos</p></div>
        """
        chapter_payload = {"data": [
            {"id": 2, "slug": "2.5", "created_at": "2026-08-03 09:10:11"},
            {"id": 10, "slug": "10", "created_at": "2026-08-04 10:11:12"},
            {"id": 3, "slug": "extra", "created_at": None},
        ]}
        reader = '<script>const pUrl = [{"imgURL":"https://cdn/1.jpg",}, {"imgURL":"https://cdn/2.jpg",},];</script>'
        fetcher = Fetcher([
            Response("https://heavenmanga.com/manga/gato", details),
            Response("https://heavenmanga.com/manga/gato", chapter_payload),
            Response("https://heavenmanga.com/manga/leer/10", reader),
        ])
        source = source_class()(fetcher)

        manga = await source.details("https://heavenmanga.com/manga/gato")
        chapters = await source.chapters("https://heavenmanga.com/manga/gato")
        pages = await source.pages(chapters[0])

        self.assertEqual((manga.description, manga.content_tags), ("Uno Dos", ("Acción", "Drama")))
        self.assertEqual([chapter.title for chapter in chapters], ["Capítulo: 10", "Capítulo: 2.5", "Capítulo: extra"])
        self.assertEqual(chapters[0].source_id, "https://heavenmanga.com/manga/gato/10#10")
        self.assertEqual(chapters[0].uploaded_at, "2026-08-04T10:11:12")
        self.assertEqual(fetcher.requests[1][2]["params"]["length"], "10000")
        self.assertEqual(fetcher.requests[1][2]["headers"], {"X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(fetcher.requests[2][1], "https://heavenmanga.com/manga/leer/10")
        self.assertEqual([page.source_id for page in pages], ["https://cdn/1.jpg", "https://cdn/2.jpg"])


if __name__ == "__main__":
    unittest.main()
