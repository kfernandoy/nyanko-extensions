from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.generate import _supported_zeistmanga, _zeistmanga_bundle


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


def entry(title, categories, url, thumbnail=None):
    item = {
        "title": {"$t": title},
        "published": {"$t": "2026-08-04T10:00:00Z"},
        "category": [{"term": category} for category in categories],
        "link": [{"rel": "alternate", "href": url}],
    }
    if thumbnail:
        item["media$thumbnail"] = {"url": thumbnail}
    return item


def source_class():
    root = Path(__file__).parents[1]
    module = root.parent / "extensions-source-main" / "src" / "es" / "gistamishouse"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_zeistmanga(module, build)
    assert config is not None
    bundle = _zeistmanga_bundle(
        # base.py, no madara.py: este motor no necesita el motor Madara.
        (root / "engines" / "base.py").read_text(encoding="utf-8"),
        (root / "engines" / "zeistmanga.py").read_text(encoding="utf-8"),
        config,
    )
    namespace = {"__name__": "test_gistamishouse_bundle"}
    exec(compile(bundle, "gistamishouse_es.py", "exec"), namespace)
    return namespace["SOURCE"]


class GistamisHouseTest(unittest.IsolatedAsyncioTestCase):
    async def test_custom_filters_exclusions_and_feed_thumbnail_match_kotlin(self):
        feed = {"feed": {"entry": [
            entry("Gato", ["Series"], "https://gistamishousefansub.blogspot.com/gato", "https://cdn/s72-c/gato.jpg"),
            entry("Novela", ["Series", "Novela"], "https://gistamishousefansub.blogspot.com/novela"),
            entry("Anime", ["Series", "Anime"], "https://gistamishousefansub.blogspot.com/anime"),
        ]}}
        fetcher = Fetcher([Response("https://gistamishousefansub.blogspot.com/feed", feed)])
        source = source_class()(fetcher)

        filters = source.get_filters()
        result = await source.search("", 1, {"status": "Activo", "type": "Manga", "genres": ["Acción"]})

        self.assertEqual([item.id for item in filters], ["status", "type", "genres"])
        self.assertEqual(filters[0].default, "Activo")
        self.assertEqual(filters[1].default, "Manga")
        self.assertIn(("Vida laboral", "Vida laboral"), filters[2].options)
        self.assertEqual(
            fetcher.requests[0][1],
            "https://gistamishousefansub.blogspot.com/feeds/posts/default/-/Series/Activo/Manga/Acci%C3%B3n",
        )
        self.assertEqual([item.title for item in result["items"]], ["Gato"])
        self.assertEqual(result["items"][0].cover_url, "https://cdn/w600/gato.jpg")
        self.assertEqual(source.capabilities.requests_per_minute, 120)
        self.assertEqual(source.capabilities.content_warning, "nsfw")

    async def test_popular_selector_excludes_chapters(self):
        html = """
        <div class="PopularPosts"><div class="grid">
          <figure><img src="https://cdn/gato.jpg"><figcaption><a href="/gato">Gato</a></figcaption></figure>
          <figure><span data="Capitulo">Capitulo</span><figcaption><a href="/cap-1">Capitulo 1</a></figcaption></figure>
        </div></div>
        """
        source = source_class()(Fetcher([Response("https://gistamishousefansub.blogspot.com", html)]))

        popular = await source.browse("popular")

        self.assertEqual([item.title for item in popular], ["Gato"])
        self.assertEqual(popular[0].cover_url, "https://cdn/gato.jpg")

    async def test_details_two_chapter_categories_and_exact_page_container(self):
        details = """
        <div class="grid gtc-235fr">
          <img src="/gato.jpg"><div id="synopsis">Uno <b>Dos</b></div>
          <div class="mt-15"><a rel="tag">Acción</a><a rel="tag">Drama</a></div>
          <div class="y6x11p">Otros Nombres <span class="dt">Cat</span></div>
          <div class="y6x11p">Estado <span class="dt">Activo</span></div>
          <div class="y6x11p">Mangaka <span class="dt">Ana</span></div>
          <div class="y6x11p">Artista <span class="dt">Leo</span></div>
        </div>
        """
        series_page = "<div id='latest'><script>const label = 'Gato';</script></div>"
        chapter_feed = {"feed": {"entry": [
            entry("Capitulo 1", ["Capitulo"], "https://gistamishousefansub.blogspot.com/cap-1"),
            entry("Capitulo 2", ["Cap"], "https://gistamishousefansub.blogspot.com/cap-2"),
            entry("Especial", ["Otro"], "https://gistamishousefansub.blogspot.com/especial"),
        ]}}
        reader = """
        <div class="post"><p><img src="https://cdn/fuera-article.jpg"></p></div>
        <article class="oh"><div class="post">
          <img src="https://cdn/fuera-p.jpg"><p><img src="https://cdn/pagina.jpg"></p>
        </div></article>
        """
        fetcher = Fetcher([
            Response("https://gistamishousefansub.blogspot.com/gato", details),
            Response("https://gistamishousefansub.blogspot.com/gato", series_page),
            Response("https://gistamishousefansub.blogspot.com/feeds/posts/default/-/Gato", chapter_feed),
            Response("https://gistamishousefansub.blogspot.com/cap-1", reader),
        ])
        source = source_class()(fetcher)

        manga = await source.details("https://gistamishousefansub.blogspot.com/gato")
        chapters = await source.chapters("https://gistamishousefansub.blogspot.com/gato")
        pages = await source.pages(chapters[0])

        self.assertEqual((manga.description, manga.author, manga.artist, manga.status), (
            "Uno Dos\n\nOtros Nombres: Cat", "Ana", "Leo", "ongoing",
        ))
        self.assertEqual(manga.content_tags, ("Acción", "Drama"))
        self.assertEqual([chapter.title for chapter in chapters], ["Capitulo 1", "Capitulo 2"])
        self.assertEqual(fetcher.requests[2][2]["params"], {
            "alt": "json", "start-index": 1, "max-results": 999999,
        })
        self.assertEqual([page.source_id for page in pages], ["https://cdn/pagina.jpg"])


if __name__ == "__main__":
    unittest.main()
