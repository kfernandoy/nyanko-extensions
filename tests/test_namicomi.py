from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.generate import _manual_bundle

API = "https://api.namicomi.com"
CDN = "https://uploads.namicomi.com"


class Response:
    def __init__(self, url: str, payload=None, code: int = 200) -> None:
        self.url = url
        self.text = json.dumps(payload if payload is not None else {})
        self.status_code = code

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


def source_class(extension_id: str = "namicomi_es"):
    path = Path(__file__).parents[1] / "engines" / "manual" / f"{extension_id}.py"
    namespace = {"__name__": f"test_{extension_id}_bundle"}
    exec(compile(_manual_bundle(path), str(path), "exec"), namespace)
    return namespace["SOURCE"]


TITLE = {
    "id": "abc",
    "type": "title",
    "attributes": {
        "title": {"en": "The Cat", "es-es": "El Gato"},
        "description": {"en": "A story.", "es-es": "Una historia."},
        "slug": "el-gato",
        "originalLanguage": "ja",
        "contentRating": "mature",
        "publicationStatus": "ongoing",
    },
    "relationships": [
        {"id": "c1", "type": "cover_art", "attributes": {"fileName": "cover.jpg"}},
        {"id": "o1", "type": "organization", "attributes": {"name": "Equipo"}},
        {"id": "romance", "type": "tag", "attributes": {"group": "genre"}},
        {"id": "action", "type": "primary_tag", "attributes": {"group": "genre"}},
        {"id": "gore", "type": "secondary_tag", "attributes": {"group": "content-warnings"}},
    ],
}


class NamiComiTest(unittest.IsolatedAsyncioTestCase):
    async def test_catalogos_ordenan_por_vistas_y_publicacion(self):
        payload = {"data": [TITLE], "meta": {"limit": 20, "offset": 0, "total": 40}}
        fetcher = Fetcher([
            Response(f"{API}/title/search", payload),
            Response(f"{API}/title/search", payload),
        ])
        source = source_class()(fetcher)

        popular = await source.browse("popular", 2)
        await source.browse("latest", 1)

        params = fetcher.requests[0][2]["params"]
        self.assertEqual(params[0], ("order[views]", "desc"))
        self.assertEqual(params[1], ("availableTranslatedLanguages[]", "es-es"))
        self.assertEqual(params[3], ("offset", "20"))
        self.assertEqual(fetcher.requests[1][2]["params"][0], ("order[publishedAt]", "desc"))
        self.assertTrue(popular["has_more"])

    async def test_ficha_resuelve_titulo_y_etiquetas_por_idioma(self):
        payload = {"data": [TITLE], "meta": {"limit": 20, "offset": 0, "total": 1}}
        fetcher = Fetcher([Response(f"{API}/title/search", payload)])
        source = source_class()(fetcher)

        manga = (await source.browse("popular"))["items"][0]

        self.assertEqual((manga.title, manga.description), ("El Gato", "Una historia."))
        self.assertEqual(manga.cover_url, f"{CDN}/covers/abc/cover.jpg")
        self.assertEqual((manga.author, manga.status), ("Equipo", "ongoing"))
        # Orden de grupos: avisos, formato, genero, tema; luego rating e idioma.
        self.assertEqual(manga.content_tags, ("Gore", "Action", "Romance", "Content rating: Mature", "Japanese"))
        self.assertEqual(manga.web_url, "https://namicomi.com/es-es/title/abc/el-gato")

    async def test_el_idioma_ingles_cae_al_titulo_en_ingles(self):
        payload = {"data": [TITLE], "meta": {"limit": 20, "offset": 0, "total": 1}}
        fetcher = Fetcher([Response(f"{API}/title/search", payload)])
        source = source_class("namicomi_en")(fetcher)

        manga = (await source.browse("popular"))["items"][0]

        self.assertEqual(manga.title, "The Cat")

    async def test_busqueda_por_id_y_por_url(self):
        payload = {"data": [TITLE], "meta": {"limit": 20, "offset": 0, "total": 1}}
        fetcher = Fetcher([
            Response(f"{API}/title/search", payload),
            Response(f"{API}/title/search", payload),
        ])
        source = source_class()(fetcher)

        await source.search("id:abc")
        await source.search("https://namicomi.com/es-es/title/abc/el-gato")

        self.assertEqual(fetcher.requests[0][2]["params"][0], ("ids[]", "abc"))
        self.assertEqual(fetcher.requests[1][2]["params"][0], ("ids[]", "abc"))

    async def test_los_filtros_arman_la_consulta(self):
        payload = {"data": [], "meta": {"limit": 20, "offset": 0, "total": 0}}
        fetcher = Fetcher([Response(f"{API}/title/search", payload)])
        source = source_class()(fetcher)

        await source.search("  el   gato  ", 1, {
            "hasAvailableChapters": True,
            "contentRatings": ["safe", "mature"],
            "publicationStatuses": ["ongoing"],
            "sort": "views", "sortDirection": "asc",
            "genre": {"romance": "include", "horror": "exclude"},
        })

        params = fetcher.requests[0][2]["params"]
        self.assertIn(("title", "el gato"), params)
        self.assertIn(("hasAvailableChapters", "true"), params)
        self.assertIn(("contentRatings[]", "safe"), params)
        self.assertIn(("contentRatings[]", "mature"), params)
        self.assertIn(("publicationStatuses[]", "ongoing"), params)
        self.assertIn(("order[views]", "asc"), params)
        self.assertIn(("includedTagsMode", "and"), params)
        self.assertIn(("excludedTagsMode", "or"), params)
        self.assertIn(("includedTags[]", "romance"), params)
        self.assertIn(("excludedTags[]", "horror"), params)

    async def test_capitulos_paginan_y_ocultan_los_bloqueados(self):
        primera = {
            "data": [
                {"id": "ch1", "type": "chapter", "relationships": [
                    {"id": "o1", "type": "organization", "attributes": {"name": "Equipo"}},
                ], "attributes": {
                    "name": "El principio", "volume": "1", "chapter": "1", "pages": 10,
                    "publishAt": "2026-08-05T12:00:00+000",
                }},
            ],
            # total > limit + offset: hay una segunda tanda.
            "meta": {"limit": 200, "offset": 0, "total": 201},
        }
        segunda = {
            "data": [
                {"id": "ch2", "type": "chapter", "relationships": [], "attributes": {
                    "name": None, "volume": None, "chapter": "2", "pages": 10,
                    "publishAt": "2026-08-06T12:00:00+000",
                }},
            ],
            "meta": {"limit": 200, "offset": 200, "total": 201},
        }
        gating = {"data": {"type": "entity_access_map", "attributes": {
            "map": {"ch1": True, "ch2": False},
        }}}
        fetcher = Fetcher([
            Response(f"{API}/chapter", primera),
            Response(f"{API}/chapter", segunda),
            Response(f"{API}/gating/check", gating),
        ])
        source = source_class()(fetcher)

        chapters = await source.chapters("abc")

        self.assertEqual(fetcher.requests[0][2]["params"][4], ("translatedLanguages[]", "es-es"))
        self.assertEqual(fetcher.requests[1][2]["params"][3], ("offset", "200"))
        self.assertEqual(fetcher.requests[2][0], "POST")
        self.assertEqual(
            fetcher.requests[2][2]["json"]["entities"],
            [{"entityId": "ch1", "entityType": "chapter"},
             {"entityId": "ch2", "entityType": "chapter"}],
        )
        # ch2 esta bloqueado y la preferencia para mostrarlos no vuelve a la fuente.
        self.assertEqual([c.source_id for c in chapters], ["ch1"])
        self.assertEqual(chapters[0].title, "Vol.1 Ch.1 - El principio")
        self.assertEqual(chapters[0].scanlator, "Equipo")
        self.assertEqual(chapters[0].uploaded_at, "2026-08-05T12:00:00")

    async def test_paginas_componen_la_ruta_con_el_hash(self):
        payload = {"data": {
            "type": "image_data", "baseUrl": CDN, "hash": "h4sh",
            "source": [{"filename": "1.jpg", "size": 1, "resolution": None},
                       {"filename": "2.jpg", "size": 1, "resolution": None}],
            "high": [], "medium": [], "low": [{"filename": "1.low.jpg", "size": 1, "resolution": None}],
        }}
        fetcher = Fetcher([Response(f"{API}/images/chapter/ch1", payload)])
        source = source_class()(fetcher)

        pages = await source.pages("ch1")

        self.assertEqual(fetcher.requests[0][2]["params"], [("newQualities", "true")])
        self.assertEqual(
            [page.source_id for page in pages],
            [f"{CDN}/chapter/ch1/h4sh/source/1.jpg", f"{CDN}/chapter/ch1/h4sh/source/2.jpg"],
        )

    async def test_respuesta_204_devuelve_vacio(self):
        fetcher = Fetcher([Response(f"{API}/title/search", None, 204)])
        source = source_class()(fetcher)

        result = await source.browse("popular")

        self.assertEqual(result, {"items": [], "has_more": False})


if __name__ == "__main__":
    unittest.main()
