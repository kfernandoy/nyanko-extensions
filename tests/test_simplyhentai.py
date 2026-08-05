from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.generate import _manual_bundle

API = "https://api.simply-hentai.com/v3"


class Response:
    def __init__(self, url: str, payload) -> None:
        self.url = url
        self.text = payload if isinstance(payload, str) else json.dumps(payload)
        self.status_code = 200

    def raise_for_status(self) -> None:
        pass

    def json(self):
        return json.loads(self.text)


class Fetcher:
    def __init__(self, responses: list[Response]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, str, dict]] = []

    async def request(self, method: str, url: str, **kwargs):
        self.requests.append((method, url, kwargs))
        return self.responses.pop(0)


def source_class(extension_id: str = "simplyhentai_es"):
    path = Path(__file__).parents[1] / "engines" / "manual" / f"{extension_id}.py"
    namespace = {"__name__": f"test_{extension_id}_bundle"}
    exec(compile(_manual_bundle(path), str(path), "exec"), namespace)
    return namespace["SOURCE"]


ALBUM = {
    "preview": {"page_num": 0, "sizes": {"full": "https://cdn/f.jpg", "thumb": "https://cdn/t.jpg"}},
    "series": {"slug": "serie", "title": "La Serie"},
    "slug": "album",
    "title": "El Álbum",
}

FICHA = {"data": {
    **ALBUM,
    "artists": [{"slug": "a", "title": "Kim"}],
    "characters": [{"slug": "c", "title": "Gato"}],
    "translators": [{"slug": "t", "title": "Equipo"}],
    "tags": [{"slug": "g", "title": "Romance"}],
    "created_at": "2026-08-05T12:00:00.000",
    "description": "Una historia.",
    "images": [],
}}


class SimplyHentaiTest(unittest.IsolatedAsyncioTestCase):
    async def test_catalogo_usa_la_etiqueta_de_idioma(self):
        payload = {"pagination": {"next": 2}, "data": {"albums": [ALBUM]}}
        fetcher = Fetcher([
            Response(f"{API}/tag/spanish", payload),
            Response(f"{API}/tag/spanish", payload),
        ])
        source = source_class()(fetcher)

        popular = await source.browse("popular", 2)
        await source.browse("latest", 1)

        self.assertEqual(fetcher.requests[0][1], f"{API}/tag/spanish")
        self.assertEqual(fetcher.requests[0][2]["params"], [("type", "language"), ("page", "2")])
        self.assertEqual(
            fetcher.requests[1][2]["params"],
            [("type", "language"), ("page", "1"), ("sort", "newest")],
        )
        self.assertEqual(
            [(item.source_id, item.title, item.cover_url) for item in popular["items"]],
            [("serie/album", "El Álbum", "https://cdn/t.jpg")],
        )
        self.assertTrue(popular["has_more"])

    async def test_cada_idioma_pide_su_propia_etiqueta(self):
        payload = {"pagination": {"next": None}, "data": {"albums": []}}
        fetcher = Fetcher([Response(f"{API}/tag/japanese", payload)])
        source = source_class("simplyhentai_ja")(fetcher)

        result = await source.browse("popular")

        self.assertEqual(fetcher.requests[0][1], f"{API}/tag/japanese")
        self.assertFalse(result["has_more"])

    async def test_busqueda_desglosa_las_listas_por_coma(self):
        payload = {"pagination": {"next": None}, "data": [{"object": ALBUM}]}
        fetcher = Fetcher([Response(f"{API}/search/complex", payload)])
        source = source_class()(fetcher)

        result = await source.search("gato", 3, {
            "sort": "popularity", "series": " La Serie ", "tags": "romance, drama",
            "artists": "kim",
        })

        self.assertEqual(fetcher.requests[0][2]["params"], [
            ("query", "gato"), ("page", "3"), ("blacklist", ""),
            ("filter[language][0]", "Spanish"),
            ("sort", "popularity"),
            ("filter[series_title][0]", "La Serie"),
            ("filter[tags][0]", "romance"), ("filter[tags][1]", "drama"),
            ("filter[artists][0]", "kim"),
        ])
        self.assertEqual([item.source_id for item in result["items"]], ["serie/album"])

    async def test_ficha_arma_la_descripcion_como_el_kotlin(self):
        fetcher = Fetcher([Response(f"{API}/manga/album", FICHA)])
        source = source_class()(fetcher)

        manga = await source.details("serie/album")

        self.assertEqual(fetcher.requests[0][1], f"{API}/manga/album")
        self.assertEqual(manga.description, "Una historia.\n\nSeries: La Serie\nCharacters: Gato")
        self.assertEqual((manga.author, manga.artist), ("Kim", "Kim"))
        self.assertEqual(manga.content_tags, ("Romance",))

    async def test_un_solo_capitulo_con_todas_las_paginas(self):
        fetcher = Fetcher([Response(f"{API}/manga/album", FICHA)])
        source = source_class()(fetcher)

        chapters = await source.chapters("serie/album")

        self.assertEqual(len(chapters), 1)
        self.assertEqual(chapters[0].source_id, "serie/album/all-pages")
        self.assertEqual((chapters[0].title, chapters[0].scanlator), ("Chapter", "Equipo"))
        self.assertEqual(chapters[0].uploaded_at, "2026-08-05T12:00:00")

    async def test_paginas_usan_el_segundo_tramo_de_la_ruta(self):
        payload = {"data": {"pages": [
            {"page_num": 0, "sizes": {"full": "https://cdn/1.jpg", "thumb": "t"}},
            {"page_num": 1, "sizes": {"full": "https://cdn/2.jpg", "thumb": "t"}},
        ]}}
        fetcher = Fetcher([Response(f"{API}/manga/album/pages", payload)])
        source = source_class()(fetcher)

        pages = await source.pages("serie/album/all-pages")

        self.assertEqual(fetcher.requests[0][1], f"{API}/manga/album/pages")
        self.assertEqual([page.source_id for page in pages], ["https://cdn/1.jpg", "https://cdn/2.jpg"])
        self.assertEqual([page.index for page in pages], [0, 1])
        self.assertEqual(source.capabilities.content_warning, "nsfw")


if __name__ == "__main__":
    unittest.main()
