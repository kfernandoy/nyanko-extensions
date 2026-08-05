from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.generate import _manual_bundle

BASE = "https://manhwascanx.lat"
API = f"{BASE}/api"


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
    path = Path(__file__).parents[1] / "engines" / "manual" / "mantrazscan_es.py"
    namespace = {"__name__": "test_mantrazscan_bundle"}
    exec(compile(_manual_bundle(path), str(path), "exec"), namespace)
    return namespace["SOURCE"]


LISTADO = {"data": {
    "series": [{"id": 7, "title": "El Gato", "slug": "el-gato", "cover_url": "/covers/7.jpg"}],
    "page": 2, "total_pages": 5,
}}


class ManhwaScanTest(unittest.IsolatedAsyncioTestCase):
    async def test_catalogos_ordenan_por_vistas_y_actualizacion(self):
        fetcher = Fetcher([Response(f"{API}/series", LISTADO), Response(f"{API}/series", LISTADO)])
        source = source_class()(fetcher)

        popular = await source.browse("popular", 2)
        await source.browse("latest", 1)

        self.assertEqual(fetcher.requests[0][2]["params"], [
            ("page", "2"), ("limit", "48"), ("sort", "views"), ("q", ""),
        ])
        self.assertEqual(fetcher.requests[1][2]["params"][2], ("sort", "updated"))
        # El id junta identificador y slug; la portada relativa se absolutiza.
        self.assertEqual(
            [(item.source_id, item.title, item.cover_url) for item in popular["items"]],
            [("7#el-gato", "El Gato", f"{BASE}/covers/7.jpg")],
        )
        self.assertTrue(popular["has_more"])

    async def test_busqueda_omite_los_filtros_vacios(self):
        fetcher = Fetcher([Response(f"{API}/series", LISTADO)])
        source = source_class()(fetcher)

        await source.search(" gato ", 3, {"genre": "romance", "status": "", "type": "bl"})

        self.assertEqual(fetcher.requests[0][2]["params"], [
            ("page", "3"), ("limit", "48"), ("q", "gato"), ("sort", "updated"),
            ("genre", "romance"), ("type", "bl"),
        ])

    async def test_ficha_y_capitulos_usan_el_identificador(self):
        ficha = {"data": {"series": {
            "id": 7, "title": "El Gato", "slug": "el-gato", "description": "Una historia.",
            "cover_url": "/covers/7.jpg", "status": "ongoing", "author": "Kim", "artist": " ",
            "genres": ["Acción", "Romance"],
        }}}
        capitulos = {"data": {"chapters": [
            {"id": 11, "chapter_num": "2.0", "title": "El principio", "slug": "cap-2",
             "created_at": "2026-08-05 12:00:00"},
            {"id": 10, "chapter_num": "1.5", "title": None, "slug": "cap-1-5", "created_at": None},
        ]}}
        fetcher = Fetcher([
            Response(f"{API}/series/7", ficha),
            Response(f"{API}/series/7/chapters", capitulos),
        ])
        source = source_class()(fetcher)

        manga = await source.details("7#el-gato")
        chapters = await source.chapters("7#el-gato")

        self.assertEqual(fetcher.requests[0][1], f"{API}/series/7")
        self.assertEqual((manga.author, manga.artist), ("Kim", None))
        self.assertEqual((manga.status, manga.content_tags), ("ongoing", ("Acción", "Romance")))
        # El sufijo ".0" se recorta y el titulo se anexa solo si existe.
        self.assertEqual(
            [(c.source_id, c.title, c.number) for c in chapters],
            [("11#cap-2", "Capítulo 2 - El principio", 2.0), ("10#cap-1-5", "Capítulo 1.5", 1.5)],
        )
        self.assertEqual(chapters[0].uploaded_at, "2026-08-05T12:00:00")

    async def test_los_ids_viejos_se_migran_por_titulo(self):
        ficha = {"data": {"series": {"id": 7, "title": "El Gato", "slug": "el-gato"}}}
        fetcher = Fetcher([
            Response(f"{API}/series", LISTADO),
            Response(f"{API}/series/7", ficha),
        ])
        source = source_class()(fetcher)

        manga = await source.details("El Gato")

        self.assertEqual(fetcher.requests[0][2]["params"], [("q", "El Gato")])
        self.assertEqual(fetcher.requests[1][1], f"{API}/series/7")
        self.assertEqual(manga.source_id, "7#el-gato")

    async def test_lector_exige_el_formato_nuevo(self):
        paginas = {"data": {"chapter": {"pages": [
            {"image_url": "/p/1.jpg"}, {"image_url": "https://cdn/2.jpg"},
        ]}}}
        fetcher = Fetcher([Response(f"{API}/chapters/11", paginas)])
        source = source_class()(fetcher)

        pages = await source.pages("11#cap-2")

        self.assertEqual(fetcher.requests[0][1], f"{API}/chapters/11")
        self.assertEqual([page.source_id for page in pages], [f"{BASE}/p/1.jpg", "https://cdn/2.jpg"])
        with self.assertRaises(ValueError):
            await source.pages("11")


if __name__ == "__main__":
    unittest.main()
