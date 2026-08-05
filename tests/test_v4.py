from __future__ import annotations

import unittest

from engines.v4 import adapt_source
from nyanko_api.sources.contract import SourceFilter


class LegacySource:
    """Filtros declarados que ningun metodo v3 acepta."""

    def get_filters(self):
        return [
            SourceFilter(
                id="genres",
                name="Géneros",
                type="group",
                options=[{"value": "9", "name": "Acción"}],
                default="",
            )
        ]

    async def search(self, query: str, limit: int = 20) -> list[str]:
        return [query, str(limit)]

    async def browse(self, kind: str, page: int = 1) -> list[str]:
        return [kind, str(page)]


class FilterAwareSource(LegacySource):
    """Mismos filtros, pero search si los consume."""

    async def search(self, query: str, page: int = 1, filters=None) -> list[str]:
        return [query, str(page), str(filters)]


class UnpaginatedSource:
    """Devuelve el catalogo entero de una vez: ni `page` ni `limit`."""

    async def search(self, query: str) -> list[str]:
        return [f"{query}-{index}" for index in range(45)]

    async def browse(self, kind: str, page: int = 1) -> list[str]:
        return [kind]


class CappedSource:
    """`limit` topado por el proveedor, como el `min(limit, 100)` de MangaDex."""

    async def search(self, query: str, limit: int = 20) -> list[str]:
        return [f"s{index}" for index in range(min(limit, 30))]

    async def browse(self, kind: str, page: int = 1) -> list[str]:
        return [kind]


class V4AdapterTest(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_results_follow_v4_contract(self):
        source = adapt_source(LegacySource)()

        search = await source.search("gato", 1, {"genre": "accion"})
        browse = await source.browse("latest", 3, {"genre": "accion"})

        # 21 = una pagina mas el sondeo de has_more.
        self.assertEqual(search.items, ["gato", "21"])
        self.assertEqual(browse.items, ["latest", "3"])
        self.assertTrue(browse.has_more)
        self.assertEqual(source.get_preferences(), [])

    async def test_filtros_que_nadie_consume_no_se_anuncian(self):
        self.assertEqual(await adapt_source(LegacySource)().get_filters(), [])

    async def test_filtros_se_anuncian_y_llegan_cuando_search_los_acepta(self):
        source = adapt_source(FilterAwareSource)()

        filters = await source.get_filters()
        search = await source.search("gato", 2, {"genre": "accion"})

        self.assertEqual(filters[0].type, "multi_select")
        self.assertEqual(filters[0].options, [("9", "Acción")])
        self.assertEqual(filters[0].default, [])
        self.assertEqual(search.items, ["gato", "2", "{'genre': 'accion'}"])

    async def test_search_sin_page_ni_limit_pagina_en_el_cliente(self):
        source = adapt_source(UnpaginatedSource)()

        first = await source.search("q", 1)
        third = await source.search("q", 3)
        fourth = await source.search("q", 4)

        self.assertEqual(first.items, [f"q-{index}" for index in range(20)])
        self.assertTrue(first.has_more)
        self.assertEqual(third.items, [f"q-{index}" for index in range(40, 45)])
        self.assertFalse(third.has_more)
        self.assertEqual(fourth.items, [])
        self.assertFalse(fourth.has_more)

    async def test_search_con_limit_lo_escala_y_recorta_el_tramo(self):
        source = adapt_source(CappedSource)()

        first = await source.search("q", 1)
        second = await source.search("q", 2)
        third = await source.search("q", 3)

        self.assertEqual(first.items, [f"s{index}" for index in range(20)])
        self.assertTrue(first.has_more)
        # El proveedor topa en 30: la segunda pagina trae el resto y corta.
        self.assertEqual(second.items, [f"s{index}" for index in range(20, 30)])
        self.assertFalse(second.has_more)
        # Sin material, la pagina siguiente sale vacia en vez de repetirse.
        self.assertEqual(third.items, [])
        self.assertFalse(third.has_more)

    async def test_search_no_repite_la_primera_pagina(self):
        source = adapt_source(LegacySource)()

        first = await source.search("gato", 1)
        second = await source.search("gato", 2)

        self.assertNotEqual(first.items, second.items)
        self.assertEqual(second.items, [])


if __name__ == "__main__":
    unittest.main()
