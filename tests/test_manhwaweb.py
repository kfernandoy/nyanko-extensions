from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.generate import _manual_bundle

API = "https://manhwawebbackend-production.up.railway.app"


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


def source_class():
    path = Path(__file__).parents[1] / "engines" / "manual" / "manhwaweb_es.py"
    namespace = {"__name__": "test_manhwaweb_bundle"}
    exec(compile(_manual_bundle(path), str(path), "exec"), namespace)
    return namespace["SOURCE"]


class ManhwaWebTest(unittest.IsolatedAsyncioTestCase):
    async def test_catalogos_deduplican_y_ordenan_como_el_kotlin(self):
        popular = {"top": {
            "manhwas_esp": [
                {"link": "/manga/gato", "numero": 5, "name": "Gato", "imagen": "https://cdn/gato.jpg"},
                {"link": "/manhwa/lobo", "numero": 90, "name": "Lobo", "imagen": "https://cdn/lobo.jpg"},
            ],
            # Repetido con el bloque esp: distinctBy se queda con la primera aparicion.
            "manhwas_raw": [
                {"link": "/manga/gato", "numero": 1, "name": "Gato RAW", "imagen": "https://cdn/x.jpg"},
                {"link": "/manhwa/oso", "numero": 40, "name": "Oso", "imagen": "https://cdn/oso.jpg"},
            ],
        }}
        latest = {"manhwas": {
            "manhwas_esp": [{"id_rel": "gato", "create": 100, "name_manhwa": "Gato", "img": "https://cdn/gato.jpg"}],
            "manhwas_raw": [{"id_rel": "oso", "create": 300, "name_manhwa": "Oso", "img": "https://cdn/oso.jpg"}],
            "_manhwas": [{"id_rel": "gato", "create": 999, "name_manhwa": "Gato dup", "img": "https://cdn/g.jpg"}],
        }}
        fetcher = Fetcher([
            Response(f"{API}/manhwa/nuevos", popular),
            Response(f"{API}/latest/new-manhwa", latest),
        ])
        source = source_class()(fetcher)

        top = await source.browse("popular")
        recent = await source.browse("latest")

        self.assertEqual(fetcher.requests[0][1], f"{API}/manhwa/nuevos")
        # Ordenado por vistas desc; "manga/" se reescribe a "manhwa/".
        self.assertEqual(
            [(item.source_id, item.title) for item in top["items"]],
            [("manhwa/lobo", "Lobo"), ("manhwa/oso", "Oso"), ("manhwa/gato", "Gato")],
        )
        self.assertFalse(top["has_more"])
        self.assertEqual(top["items"][0].web_url, "https://manhwaweb.com/manhwa/lobo")
        # Ordenado por fecha desc, sin repetir el slug que llega en dos bloques.
        self.assertEqual(
            [item.source_id for item in recent["items"]], ["manhwa/oso", "manhwa/gato"],
        )
        self.assertFalse(recent["has_more"])

    async def test_busqueda_arma_los_parametros_de_la_libreria(self):
        payload = {
            "data": [{"real_id": "gato", "the_real_name": "Gato", "_imagen": "https://cdn/gato.jpg"}],
            "next": True,
        }
        fetcher = Fetcher([Response(f"{API}/manhwa/library", payload)])
        source = source_class()(fetcher)

        result = await source.search("gato", 3, {
            "tipo": "manhwa", "estado": "publicandose", "generes": ["3", "2"],
            "order_item": "creacion", "order_dir": "asc",
        })

        self.assertEqual(fetcher.requests[0][2]["params"], [
            ("buscar", "gato"),
            ("tipo", "manhwa"), ("demografia", ""), ("estado", "publicandose"), ("erotico", ""),
            ("generes", "3a2"),
            ("order_dir", "asc"), ("order_item", "creacion"),
            ("page", "2"),
        ])
        self.assertEqual(result["items"][0].source_id, "manhwa/gato")
        self.assertTrue(result["has_more"])

    async def test_ficha_capitulos_y_paginas(self):
        detalle = {
            "_id": "abc123", "real_id": "gato",
            "name_esp": "Gato", "_sinopsis": "Una historia.", "_name": "Neko",
            "_status": "publicandose", "_imagen": "https://cdn/gato.jpg",
            "_categoris": [{"3": "Acción"}, {"2": "Romance"}],
            "_extras": {"autores": ["Kim", "Lee"]},
            "chapters": [
                {"chapter": 2.0, "link": "/leer/abc123-2", "create": 1754352000000},
                {"chapter": 3.5, "link_raw": "/leer/abc123-3", "create": 1754438400000},
                # Descartados: sin fecha y sin ningun enlace.
                {"chapter": 4.0, "link": "/leer/abc123-4", "create": None},
                {"chapter": 5.0, "create": 1754438400000},
            ],
        }
        paginas = {"chapter": {"img": ["https://cdn/1.jpg", "no-es-url", "https://cdn/2.jpg"]}}
        fetcher = Fetcher([
            Response(f"{API}/manhwa/see/gato", detalle),
            Response(f"{API}/manhwa/see/gato", detalle),
            Response(f"{API}/chapters/see/gato-3", paginas),
        ])
        source = source_class()(fetcher)

        manga = await source.details("manhwa/gato")
        chapters = await source.chapters("manhwa/gato")
        pages = await source.pages(chapters[0])

        self.assertEqual(fetcher.requests[0][1], f"{API}/manhwa/see/gato")
        self.assertEqual(manga.description, "Una historia.\n\nNombres alternativos: Neko")
        self.assertEqual((manga.status, manga.author), ("ongoing", "Kim, Lee"))
        self.assertEqual(manga.content_tags, ("Acción", "Romance"))
        # Ordenados por numero desc; el id interno se reemplaza por el real.
        self.assertEqual(
            [(c.title, c.source_id, c.scanlator) for c in chapters],
            [
                ("Capítulo 3.5", "leer/gato-3", "Raw"),
                ("Capítulo 2", "leer/gato-2", "Esp"),
            ],
        )
        self.assertEqual(chapters[1].uploaded_at, "2025-08-05T00:00:00")
        self.assertEqual([page.source_id for page in pages], ["https://cdn/1.jpg", "https://cdn/2.jpg"])
        self.assertEqual([page.index for page in pages], [0, 1])

    async def test_metadatos_y_cabeceras(self):
        source = source_class()(None)

        self.assertEqual(source.capabilities.content_warning, "mixed")
        self.assertEqual(source.capabilities.requests_per_minute, 120)
        self.assertEqual(source.capabilities.headers["Referer"], "https://manhwaweb.com/")
        self.assertEqual(source.image_headers["Referer"], "https://manhwaweb.com/")
        self.assertEqual(
            [value.id for value in source.get_filters()],
            ["tipo", "demografia", "estado", "erotico", "generes", "order_item", "order_dir"],
        )
        self.assertEqual(source.get_filters()[4].options[0], ("3", "Acción"))


if __name__ == "__main__":
    unittest.main()
