from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.generate import _manual_bundle

BASE = "https://platinumlilyscan.com"


class Response:
    def __init__(self, url: str, payload) -> None:
        self.url = url
        self.text = json.dumps(payload)
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


def source_class():
    path = Path(__file__).parents[1] / "engines" / "manual" / "platinumlilyscan_es.py"
    namespace = {"__name__": "test_platinumlilyscan_bundle"}
    exec(compile(_manual_bundle(path), str(path), "exec"), namespace)
    return namespace["SOURCE"]


CATALOGO = [
    {"title": "El Gato", "slug": "el-gato", "altTitles": "Neko", "coverUrl": "/covers/g.jpg",
     "author": "Kim", "artist": "Lee", "status": "ONGOING", "type": "MANHWA",
     "contentRating": "SAFE", "updatedAt": "2026-08-05T10:00:00.000Z",
     "_count": {"bookmarks": 10}, "genres": [{"genre": {"name": "Romance"}}]},
    {"title": "El Lobo", "slug": "el-lobo", "status": "COMPLETED", "type": "MANGA",
     "contentRating": "NSFW", "updatedAt": "2026-08-01T10:00:00.000Z",
     "_count": {"bookmarks": 99}, "genres": [{"genre": {"name": "Drama"}}]},
]


class PlatinumLilyScanTest(unittest.IsolatedAsyncioTestCase):
    async def test_populares_ordenan_por_marcadores_y_recientes_por_fecha(self):
        fetcher = Fetcher([
            Response(f"{BASE}/api/series", CATALOGO),
            Response(f"{BASE}/api/series", CATALOGO),
        ])
        source = source_class()(fetcher)

        popular = await source.browse("popular")
        latest = await source.browse("latest")

        self.assertEqual(fetcher.requests[0][1], f"{BASE}/api/series")
        self.assertEqual([item.source_id for item in popular["items"]], ["el-lobo", "el-gato"])
        self.assertEqual([item.source_id for item in latest["items"]], ["el-gato", "el-lobo"])
        self.assertEqual(popular["items"][1].cover_url, f"{BASE}/covers/g.jpg")
        self.assertEqual((popular["items"][1].author, popular["items"][1].status), ("Kim", "ongoing"))
        self.assertFalse(popular["has_more"])

    async def test_la_busqueda_mira_tambien_los_titulos_alternativos(self):
        fetcher = Fetcher([Response(f"{BASE}/api/series", CATALOGO)])
        source = source_class()(fetcher)

        result = await source.search("neko")

        self.assertEqual([item.source_id for item in result["items"]], ["el-gato"])

    async def test_los_filtros_se_aplican_en_el_cliente(self):
        fetcher = Fetcher([
            Response(f"{BASE}/api/series", CATALOGO),
            Response(f"{BASE}/api/series", CATALOGO),
            Response(f"{BASE}/api/series", CATALOGO),
        ])
        source = source_class()(fetcher)

        tipo = await source.search("", 1, {"type": "MANGA"})
        rating = await source.search("", 1, {"contentRating": "SAFE"})
        genero = await source.search("", 1, {"genre": "drama"})

        self.assertEqual([item.source_id for item in tipo["items"]], ["el-lobo"])
        self.assertEqual([item.source_id for item in rating["items"]], ["el-gato"])
        # El genero se compara sin distinguir mayusculas.
        self.assertEqual([item.source_id for item in genero["items"]], ["el-lobo"])

    async def test_capitulos_y_paginas_salen_de_la_misma_ficha(self):
        ficha = {
            **CATALOGO[0],
            "chapters": [
                {"id": "c2", "number": 2.0, "title": "El giro",
                 "publishedAt": "2026-08-05T10:00:00.000Z",
                 "pages": [{"imageUrl": "/p/1.jpg"}, {"imageUrl": "/p/2.jpg"}]},
                {"id": "", "number": 3.0},
                {"id": "c1", "number": 1.5, "title": None,
                 "publishedAt": "2026-08-01T10:00:00.000Z", "pages": []},
            ],
        }
        fetcher = Fetcher([
            Response(f"{BASE}/api/series/el-gato", ficha),
            Response(f"{BASE}/api/series/el-gato", ficha),
        ])
        source = source_class()(fetcher)

        chapters = await source.chapters("el-gato")
        pages = await source.pages(chapters[0])

        # El capitulo sin id se descarta.
        self.assertEqual(
            [(c.source_id, c.title, c.number) for c in chapters],
            [("el-gato#c2", "Capítulo 2 - El giro", 2.0), ("el-gato#c1", "Capítulo 1.5", 1.5)],
        )
        self.assertEqual(chapters[0].uploaded_at, "2026-08-05T10:00:00")
        self.assertEqual([page.source_id for page in pages], [f"{BASE}/p/1.jpg", f"{BASE}/p/2.jpg"])

    async def test_capitulo_inexistente_se_avisa(self):
        fetcher = Fetcher([Response(f"{BASE}/api/series/el-gato", {**CATALOGO[0], "chapters": []})])
        source = source_class()(fetcher)

        with self.assertRaises(Exception):
            await source.pages("el-gato#c9")


if __name__ == "__main__":
    unittest.main()
