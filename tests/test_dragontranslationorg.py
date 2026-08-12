from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.generate import _manual_bundle


class Response:
    def __init__(self, url, payload, status=200):
        self.url, self.status_code = url, status
        self.text = payload if isinstance(payload, str) else json.dumps(payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise ValueError(self.status_code)


class Fetcher:
    def __init__(self, responses):
        self.responses, self.requests = responses, []

    async def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return self.responses.pop(0)


def source_class():
    root = Path(__file__).parents[1]
    bundle = _manual_bundle(
        root / "engines" / "manual" / "dragontranslationorg_es.py",
        (root / "engines" / "madara.py").read_text(encoding="utf-8"),
    )
    namespace = {"__name__": "test_dragontranslationorg_bundle"}
    exec(compile(bundle, "dragontranslationorg_es.py", "exec"), namespace)
    return namespace["SOURCE"]


CARD = '''<div id="mkAgrid"><a class="acard" href="/manga/dragon/">
    <div class="ac-t"><span>No incluir</span>Dragon</div><img data-src="/cover.jpg"></a></div>
    <div class="wp-pagenavi"><a class="nextpostslink">Siguiente</a></div>'''


class DragonTranslationOrgTest(unittest.IsolatedAsyncioTestCase):
    async def test_custom_cards_pagination_and_search_filters_match_kotlin(self):
        genres = '''<div class="checkbox-group"><div class="checkbox">
            <label>Accion</label><input type="checkbox" value="accion"></div></div>'''
        fetcher = Fetcher([
            Response("https://dragontranslation.org/?s=genre&post_type=wp-manga", genres),
            Response("https://dragontranslation.org/manga/page/2/?m_orderby=views", CARD),
            Response("https://dragontranslation.org/manga/?m_orderby=latest", CARD),
            Response("https://dragontranslation.org/page/3/?s=drag", CARD),
        ])
        source = source_class()(fetcher)

        filters = await source.get_filters()
        popular = await source.browse("popular", 2)
        latest = await source.browse("latest")
        search = await source.search("drag", 3, {
            "author": "Ana", "status": ["on-going"], "adult": "0",
            "genre_condition": "1", "genres": ["accion"],
        })

        self.assertEqual([item.id for item in filters][-2:], ["genre_condition", "genres"])
        self.assertEqual(fetcher.requests[1][1], "https://dragontranslation.org/manga/page/2/")
        self.assertEqual(fetcher.requests[1][2]["params"], {"m_orderby": "views"})
        self.assertEqual(popular["items"][0].title, "Dragon")
        self.assertEqual(popular["items"][0].cover_url, "https://dragontranslation.org/cover.jpg")
        self.assertTrue(popular["has_more"])
        self.assertEqual(fetcher.requests[2][1], "https://dragontranslation.org/manga/")
        self.assertEqual(fetcher.requests[2][2]["params"], {"m_orderby": "latest"})
        self.assertEqual(latest["items"][0].title, "Dragon")
        self.assertEqual(fetcher.requests[3][1], "https://dragontranslation.org/page/3/")
        params = fetcher.requests[3][2]["params"]
        self.assertIn(("status[]", "on-going"), params)
        self.assertIn(("adult", "0"), params)
        self.assertIn(("op", "1"), params)
        self.assertIn(("genre[]", "accion"), params)
        self.assertEqual(search["items"][0].title, "Dragon")
        self.assertEqual(source.capabilities.requests_per_minute, 180)
        self.assertEqual(source.capabilities.content_warning, "mixed")

    async def test_json_chapters_and_reader_selector_match_overrides(self):
        chapters = '''<script id="mk-chapters-data">{"items":[
            {"name":"Capitulo 9", "url":"/leer/9/", "ago":"agosto 04, 2026"},
            {"name":"Especial", "url":"https://cdn.example/especial", "ago":"sin fecha"}
        ]}</script>'''
        reader = '''<div class="reading-content"><img src="/logo.jpg">
            <div class="text-left"><figure><img src="/page-2.jpg"></figure></div></div>
            <div class="page-break"><img data-src="/page-1.jpg"></div>'''
        fetcher = Fetcher([
            Response("https://dragontranslation.org/manga/dragon/", chapters),
            Response("https://dragontranslation.org/leer/9/", reader),
        ])
        source = source_class()(fetcher)

        result = await source.chapters("https://dragontranslation.org/manga/dragon/")
        pages = await source.pages(result[0])

        self.assertEqual([chapter.title for chapter in result], ["Capitulo 9", "Especial"])
        self.assertEqual(result[0].source_id, "https://dragontranslation.org/leer/9/")
        self.assertEqual(result[0].uploaded_at, "2026-08-04T00:00:00")
        self.assertIsNone(result[1].uploaded_at)
        self.assertEqual([page.source_id for page in pages], [
            "https://dragontranslation.org/page-2.jpg",
            "https://dragontranslation.org/page-1.jpg",
        ])

    async def test_direct_url_search_uses_custom_details_selectors(self):
        details = '''<div class="hcol"><h1 class="htitle">Dragon</h1>
            <div class="htags"><span class="htag--status">Completado</span></div>
            <div class="hchips--genres"><a class="chip">Accion</a></div></div>
            <div id="syn"><p>Uno</p><p>Dos</p></div>
            <div class="hposter__card"><img src="/poster.jpg"></div>
            <div class="author-content"><a>Ana</a></div><div class="artist-content"><a>Leo</a></div>'''
        fetcher = Fetcher([Response("https://dragontranslation.org/manga/dragon/", details)])
        source = source_class()(fetcher)

        result = await source.search("https://dragontranslation.org/manga/dragon/")

        item = result["items"][0]
        self.assertEqual(fetcher.requests[0][1], "https://dragontranslation.org/manga/dragon/")
        self.assertEqual((item.title, item.status), ("Dragon", "completed"))
        self.assertEqual(item.description, "Uno\n\nDos")
        self.assertEqual((item.author, item.artist), ("Ana", "Leo"))
        self.assertEqual(item.content_tags, ("Accion",))
        self.assertEqual(item.cover_url, "https://dragontranslation.org/poster.jpg")


if __name__ == "__main__":
    unittest.main()
