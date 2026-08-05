from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.generate import _madara_bundle, _supported_madara


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
    module = root.parent / "extensions-source-main" / "src" / "es" / "emperorscan"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_madara(module, build)
    assert config is not None
    bundle = _madara_bundle((root / "engines" / "madara.py").read_text(encoding="utf-8"), config)
    namespace = {"__name__": "test_emperorscan_bundle"}
    exec(compile(bundle, "emperorscan_es.py", "exec"), namespace)
    return namespace["SOURCE"]


class EmperorScanTest(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_preferences_and_metadata_match_kotlin(self):
        html = '''<div id="mkAgrid"><a class="acard" href="/manga/reino/">
            <div class="ac-t"><span>Extra</span>Reino</div><img src="/reino.jpg"></a></div>
            <div class="wp-pagenavi"><a class="nextpostslink">Siguiente</a></div>'''
        fetcher = Fetcher([Response("https://imperiomanhua.com/manga/?m_orderby=views", html)])
        source = source_class()(fetcher)

        result = await source.browse("popular")
        preferences = source.get_preferences()

        self.assertEqual(result["items"][0].title, "Reino")
        self.assertTrue(result["has_more"])
        self.assertEqual([item.id for item in preferences], [
            "random_user_agent", "custom_user_agent", "removePremiumChapters",
        ])
        self.assertEqual([item.default for item in preferences], ["off", "", True])
        self.assertEqual(source.capabilities.headers["User-Agent"], "Nyanko/0.2.4")
        self.assertEqual(source.capabilities.requests_per_minute, 120)
        self.assertEqual(source.capabilities.content_warning, "safe")

    async def test_default_preference_filters_every_kotlin_premium_marker(self):
        payload = {"items": [
            {"name": "Capitulo 1", "url": "/leer/1/", "ago": "agosto 04, 2026", "st": "open"},
            {"name": "VIP 2", "url": "/leer/2/", "ago": "", "st": "open"},
            {"name": "Soberano 3", "url": "/leer/3/", "ago": "", "st": "open"},
            {"name": "Premium 4", "url": "/leer/4/", "ago": "", "st": "open"},
            {"name": "Capitulo 5", "url": "/membership-levels/vip/", "ago": "", "st": "open"},
            {"name": "Capitulo 6", "url": "/leer/6/", "ago": "", "st": "locked"},
        ]}
        html = f'<script id="mk-chapters-data">{json.dumps(payload)}</script>'
        reader = '''<div class="reading-content"><img src="/logo.jpg">
            <div class="text-left"><figure><img src="/pagina.jpg"></figure></div></div>'''
        fetcher = Fetcher([
            Response("https://imperiomanhua.com/manga/reino/", html),
            Response("https://imperiomanhua.com/leer/1/", reader),
        ])
        source = source_class()(fetcher)

        chapters = await source.chapters("https://imperiomanhua.com/manga/reino/")
        pages = await source.pages(chapters[0])

        self.assertEqual([chapter.title for chapter in chapters], ["Capitulo 1"])
        self.assertEqual(chapters[0].source_id, "https://imperiomanhua.com/leer/1/")
        self.assertEqual(chapters[0].uploaded_at, "2026-08-04T00:00:00")
        self.assertEqual([page.source_id for page in pages], ["https://imperiomanhua.com/pagina.jpg"])

    async def test_details_remove_discord_and_premium_categories(self):
        details = '''<div class="hcol"><h1 class="htitle">Reino</h1>
            <div class="htags"><span class="htag--status">En curso</span></div>
            <div class="hchips--genres"><a class="chip">Accion</a><a class="chip">VIP</a></div>
            <div class="hchips--tags"><a class="chip">Aventura</a><a class="chip">Premium</a>
                <a class="chip">Emperor Scan</a><a class="chip">Read Online</a></div></div>
            <div id="syn"><p>Sinopsis</p><p>HAZ CLICK AQUÍ PARA UNIRTE A NUESTRO DISCORD</p></div>
            <div class="hposter__card"><img src="/poster.jpg"></div>'''
        fetcher = Fetcher([Response("https://imperiomanhua.com/manga/reino/", details)])
        source = source_class()(fetcher)

        result = await source.search("https://imperiomanhua.com/manga/reino/")

        item = result["items"][0]
        self.assertEqual(item.description, "Sinopsis")
        self.assertEqual(item.content_tags, ("Accion", "Aventura"))
        self.assertEqual(item.status, "ongoing")


if __name__ == "__main__":
    unittest.main()
