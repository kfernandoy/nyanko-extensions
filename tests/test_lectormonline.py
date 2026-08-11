"""Regresiones de Gato Libreria (gatolibreria.com, antes mangolibreria.com).

El sitio se rehizo dos veces: primero Next.js, luego SvelteKit, y ademas cambio de
dominio. Los casos de aqui salen de fallos vistos en la validacion humana, no de
suposiciones sobre el markup.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.generate import _manual_bundle

BASE = "https://gatolibreria.com"


class Response:
    def __init__(self, url: str, payload):
        self.url = url
        self.status_code = 200
        self._payload = payload
        self.text = payload if isinstance(payload, str) else json.dumps(payload)

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload if not isinstance(self._payload, str) else json.loads(self._payload)


class Fetcher:
    def __init__(self, responses):
        self.responses, self.requests = responses, []

    async def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return self.responses.pop(0)


def source_class():
    path = Path(__file__).parents[1] / "engines" / "manual" / "lectormonline_es.py"
    namespace = {"__name__": "test_lectormonline_bundle"}
    exec(compile(_manual_bundle(path), str(path), "exec"), namespace)
    return namespace["SOURCE"]


def comic(slug: str, titulo: str, generos) -> dict:
    return {
        "slug": slug,
        "title": titulo,
        "coverImage": f"https://cdn/{slug}.jpg",
        "description": "Sinopsis",
        "status": "ongoing",
        "genres": generos,
    }


class GatoLibreriaTest(unittest.IsolatedAsyncioTestCase):
    async def test_apunta_al_dominio_nuevo(self):
        """mangolibreria.com ya ni presenta un certificado valido."""
        self.assertEqual(source_class().base_url, BASE)

    async def test_las_etiquetas_son_el_nombre_y_no_el_objeto_entero(self):
        """La API devuelve objetos, no cadenas.

        Cada genero llega como
        ``{"id": 13, "name": "Drama", "slug": "drama", "createdAt": "..."}``; al pasarlo
        por ``str()`` la ficha mostraba el diccionario completo como etiqueta.
        """
        generos = [
            {"id": 13, "name": "Drama", "slug": "drama", "createdAt": "2026-01-28T22:41:08.517Z"},
            {"id": 23, "name": "Misterio", "slug": "misterio", "createdAt": "2026-01-28T22:46:33.494Z"},
            {"id": 40, "name": "Romance", "slug": "romance", "createdAt": "2026-01-29T01:42:25.100Z"},
        ]
        fetcher = Fetcher([
            Response(f"{BASE}/api/comics", {
                "data": [comic("gato", "El Gato", generos)],
                "pagination": {"page": 1, "totalPages": 3},
            }),
        ])
        source = source_class()(fetcher)

        resultado = await source.browse("popular", 1)

        self.assertEqual(
            resultado["items"][0].content_tags, ("Drama", "Misterio", "Romance"),
        )
        self.assertTrue(resultado["has_more"])

    async def test_una_etiqueta_ya_aplanada_sigue_valiendo(self):
        fetcher = Fetcher([
            Response(f"{BASE}/api/comics", {
                "data": [comic("gato", "El Gato", ["Drama", "  Romance  "])],
                "pagination": {"page": 1, "totalPages": 1},
            }),
        ])
        source = source_class()(fetcher)

        resultado = await source.browse("popular", 1)

        self.assertEqual(resultado["items"][0].content_tags, ("Drama", "Romance"))
        self.assertFalse(resultado["has_more"])

    async def test_un_genero_sin_nombre_no_deja_una_etiqueta_vacia(self):
        fetcher = Fetcher([
            Response(f"{BASE}/api/comics", {
                "data": [comic("gato", "El Gato", [
                    {"id": 1, "slug": "solo-slug"},
                    {"id": 2},
                    {"id": 3, "name": "Drama"},
                ])],
                "pagination": {"page": 1, "totalPages": 1},
            }),
        ])
        source = source_class()(fetcher)

        resultado = await source.browse("popular", 1)

        self.assertEqual(resultado["items"][0].content_tags, ("solo-slug", "Drama"))

    async def test_la_busqueda_usa_el_parametro_search(self):
        """Con `title` o `q` la API responde 200 pero ignora el filtro."""
        fetcher = Fetcher([
            Response(f"{BASE}/api/comics", {
                "data": [comic("gato", "El Gato", [])],
                "pagination": {"page": 1, "totalPages": 1},
            }),
        ])
        source = source_class()(fetcher)

        await source.search(" gato ", 1)

        self.assertEqual(fetcher.requests[0][2]["params"]["search"], "gato")


if __name__ == "__main__":
    unittest.main()
