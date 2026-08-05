from __future__ import annotations

import io
import unittest
from pathlib import Path

from PIL import Image

from tools.generate import _extract_kotlin_metadata, _generic_bundle, _supported_generic


class Response:
    def __init__(self, url, text="", content=b"", status=200, headers=None):
        self.url, self.text, self.content = url, text, content
        self.status_code, self.headers = status, headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise ValueError(self.status_code)


class Fetcher:
    def __init__(self, responses):
        self.responses, self.requests = responses, []

    async def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return self.responses.pop(0)


def source_class(language="es"):
    root = Path(__file__).parents[1]
    module = root.parent / "extensions-source-main" / "src" / "all" / "dragonballmultiverse"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_generic(module, build, f'lang = "{language}"\nbaseUrl = "https://www.dragonball-multiverse.com"')
    assert config is not None
    config["content_warning"] = _extract_kotlin_metadata(module)
    bundle = _generic_bundle(
        (root / "engines" / "madara.py").read_text(encoding="utf-8"),
        (root / "engines" / "generic.py").read_text(encoding="utf-8"),
        config,
    )
    namespace = {"__name__": "test_dragonballmultiverse_bundle"}
    exec(compile(bundle, f"dragonballmultiverse_{language}.py", "exec"), namespace)
    return namespace["SOURCE"]


class DragonBallMultiverseTest(unittest.IsolatedAsyncioTestCase):
    async def test_latin_american_spanish_uses_kotlin_internal_language(self):
        fetcher = Fetcher([Response(
            "https://www.dragonball-multiverse.com/es_CO/read.html",
            '<div id="dbm-reads"><div class="dbm-read"><h3>DBM Latino</h3><a href="/es_CO/dbm.html"></a></div></div>',
        )])
        source = source_class("es-419")(fetcher)

        items = await source.browse("popular")

        self.assertEqual(source.name, "dragonballmultiverse_es_419")
        self.assertEqual(fetcher.requests[0][1], "https://www.dragonball-multiverse.com/es_CO/read.html")
        self.assertEqual(items[0].title, "DBM Latino")

    async def test_spanish_catalog_is_exact_and_has_no_search_or_latest(self):
        html = '''<section id="dbm-reads">
            <article class="dbm-read"><div>Serie completa</div><h3>DB Multiverse</h3>
            <a href="/es/serie.html"><img src="/covers/dbm.jpg"></a></article>
        </section><article class="dbm-read"><h3>Fuera</h3><a href="/fuera"></a></article>'''
        fetcher = Fetcher([Response("https://www.dragonball-multiverse.com/es/read.html", html)])
        source = source_class()(fetcher)

        items = await source.browse("popular", 9)

        self.assertEqual(fetcher.requests[0][1], "https://www.dragonball-multiverse.com/es/read.html")
        self.assertEqual([(item.title, item.description) for item in items], [("DB Multiverse", "Serie completa")])
        self.assertEqual(items[0].cover_url, "https://www.dragonball-multiverse.com/covers/dbm.jpg")
        self.assertEqual(await source.search("db"), [])
        self.assertEqual(await source.browse("latest"), [])
        self.assertFalse(source.capabilities.search)
        self.assertEqual(source.capabilities.content_warning, "safe")

    async def test_chapters_pages_and_translated_balloons_match_kotlin_flow(self):
        chapters_html = '''<div class="cadrelect chapter"><a href="/es/chapter-1.html"></a><h4>Capitulo 1</h4></div>
            <div class="cadrelect chapter"><a href="/es/chapter-2.html"></a><h4>Capitulo 2</h4></div>'''
        pages_html = '<div class="pageslist"><a href="/es/page-1.html">1</a><a href="/es/page-2.html">2</a></div>'
        first_page = '''<div id="balloonsimg" src="/pages/one.jpg" style="transform: scale(2)">
            <span class="balloon" style="left:10px; top:20px; width:40px">Hola mundo</span></div>'''
        second_page = '<div id="balloonsimg"><img src="/pages/two.png"></div>'
        raw = io.BytesIO()
        Image.new("RGB", (200, 120), "white").save(raw, "JPEG")
        fetcher = Fetcher([
            Response("https://www.dragonball-multiverse.com/es/serie.html", chapters_html),
            Response("https://www.dragonball-multiverse.com/es/chapter-2.html", pages_html),
            Response("https://www.dragonball-multiverse.com/es/page-1.html", first_page),
            Response("https://www.dragonball-multiverse.com/es/page-2.html", second_page),
            Response("https://www.dragonball-multiverse.com/pages/one.jpg", content=raw.getvalue(), headers={"Content-Type": "image/jpeg"}),
        ])
        source = source_class()(fetcher)

        chapters = await source.chapters("https://www.dragonball-multiverse.com/es/serie.html")
        pages = await source.pages(chapters[0])
        content = await source.page_bytes(pages[0])

        self.assertEqual([chapter.title for chapter in chapters], ["Capitulo 2", "Capitulo 1"])
        self.assertEqual([page.filename for page in pages], ["one.jpg", "two.png"])
        self.assertIn("%22scale%22%3A2.0", pages[0].source_id)
        self.assertEqual(pages[1].source_id, "https://www.dragonball-multiverse.com/pages/two.png")
        self.assertEqual(fetcher.requests[-1][1], "https://www.dragonball-multiverse.com/pages/one.jpg")
        self.assertEqual(fetcher.requests[-1][2]["headers"]["Referer"], chapters[0].source_id)
        rendered = b"".join(content.chunks)
        self.assertEqual(content.media_type, "image/jpeg")
        self.assertTrue(rendered.startswith(b"\xff\xd8"))
        self.assertNotEqual(rendered, raw.getvalue())


if __name__ == "__main__":
    unittest.main()
