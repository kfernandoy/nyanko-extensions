from __future__ import annotations

import unittest

from engines.v4 import adapt_source
from nyanko_api.sources.contract import SourceFilter


class LegacySource:
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


class V4AdapterTest(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_results_follow_v4_contract(self):
        source = adapt_source(LegacySource)()

        search = await source.search("gato", 3, {"genre": "accion"})
        browse = await source.browse("latest", 3, {"genre": "accion"})

        self.assertEqual(search.items, ["gato", "20"])
        self.assertFalse(search.has_more)
        self.assertEqual(browse.items, ["latest", "3"])
        self.assertTrue(browse.has_more)
        filters = await source.get_filters()
        self.assertEqual(filters[0].type, "multi_select")
        self.assertEqual(filters[0].options, [("9", "Acción")])
        self.assertEqual(filters[0].default, [])
        self.assertEqual(source.get_preferences(), [])


if __name__ == "__main__":
    unittest.main()
