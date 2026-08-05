from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.generate import _manual_bundle

BASE = "https://olympusxyz.com"
PANEL = "https://panel.olympusxyz.com"


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


def source_class(fetch_domain: bool = False):
    path = Path(__file__).parents[1] / "engines" / "manual" / "olympusscanlation_es.py"
    namespace = {"__name__": "test_olympusscanlation_bundle"}
    exec(compile(_manual_bundle(path), str(path), "exec"), namespace)
    source = namespace["SOURCE"]
    source.fetch_domain = fetch_domain
    return source


LISTA = {"data": [
    {"id": 7, "name": "El Gato", "slug": "el-gato", "type": "comic", "cover": "https://cdn/g.jpg"},
    {"id": 8, "name": "La Novela", "slug": "la-novela", "type": "novel"},
]}


class OlympusScanlationTest(unittest.IsolatedAsyncioTestCase):
    async def test_el_ranking_descarta_lo_que_no_es_comic(self):
        ranking = {"data": [
            {"id": 7, "name": "El Gato", "slug": "el-gato", "type": "comic"},
            {"id": 8, "name": "La Novela", "slug": "la-novela", "type": "novel"},
        ], "current_page": 1, "last_page": 3}
        fetcher = Fetcher([
            Response(f"{BASE}/api/series/list", LISTA),
            Response(f"{BASE}/api/rankings", ranking),
        ])
        source = source_class()(fetcher)

        result = await source.browse("popular")

        self.assertEqual(fetcher.requests[0][1], f"{BASE}/api/series/list")
        self.assertEqual(
            fetcher.requests[1][2]["params"], {"page": "1", "period": "total_ranking"},
        )
        self.assertEqual([item.source_id for item in result["items"]], ["7"])
        self.assertTrue(result["has_more"])

    async def test_la_busqueda_pagina_el_listado_cacheado(self):
        muchos = {"data": [
            {"id": i, "name": f"Gato {i}", "slug": f"gato-{i}", "type": "comic"}
            for i in range(25)
        ]}
        fetcher = Fetcher([Response(f"{BASE}/api/series/list", muchos)])
        source = source_class()(fetcher)

        primera = await source.search("gato", 1)
        segunda = await source.search("gato", 2)

        # Una sola peticion: el listado queda cacheado una hora.
        self.assertEqual(len(fetcher.requests), 1)
        self.assertEqual(len(primera["items"]), 20)
        self.assertTrue(primera["has_more"])
        self.assertEqual(len(segunda["items"]), 5)
        self.assertFalse(segunda["has_more"])

    async def test_la_ficha_resuelve_el_slug_desde_el_listado(self):
        ficha = {"data": {
            "id": 7, "name": "El Gato", "slug": "el-gato", "summary": "Una historia.",
            "cover": "https://cdn/g.jpg", "status": {"id": 4},
            "genres": [{"id": 1, "name": " Acción "}],
        }}
        fetcher = Fetcher([
            Response(f"{BASE}/api/series/list", LISTA),
            Response(f"{BASE}/api/series/el-gato", ficha),
        ])
        source = source_class()(fetcher)

        manga = await source.details("7")

        self.assertEqual(fetcher.requests[1][1], f"{BASE}/api/series/el-gato")
        self.assertEqual(fetcher.requests[1][2]["params"], {"type": "comic"})
        self.assertEqual((manga.status, manga.content_tags), ("completed", ("Acción",)))

    async def test_los_capitulos_salen_del_panel_y_paginan_por_total(self):
        primera = {"data": [
            {"id": 100, "name": "2", "published_at": "2026-08-05T12:00:00.000000Z"},
        ], "meta": {"total": 2}}
        segunda = {"data": [
            {"id": 99, "name": "1", "published_at": "2026-08-01T12:00:00.000000Z"},
        ], "meta": {"total": 2}}
        fetcher = Fetcher([
            Response(f"{BASE}/api/series/list", LISTA),
            Response(f"{PANEL}/api/series/el-gato/chapters", primera),
            Response(f"{PANEL}/api/series/el-gato/chapters", segunda),
        ])
        source = source_class()(fetcher)

        chapters = await source.chapters("7")

        self.assertEqual(fetcher.requests[1][1], f"{PANEL}/api/series/el-gato/chapters")
        self.assertEqual(fetcher.requests[2][2]["params"]["page"], "2")
        self.assertEqual(
            [(c.source_id, c.title, c.number) for c in chapters],
            [("7/100", "Capitulo 2", 2.0), ("7/99", "Capitulo 1", 1.0)],
        )
        self.assertEqual(chapters[0].uploaded_at, "2026-08-05T12:00:00")

    async def test_el_lector_compone_la_ruta_con_el_slug(self):
        paginas = {"chapter": {"pages": ["https://cdn/1.jpg", "https://cdn/2.jpg"]}}
        fetcher = Fetcher([
            Response(f"{BASE}/api/series/list", LISTA),
            Response(f"{BASE}/api/capitulo/comic-el-gato/100", paginas),
        ])
        source = source_class()(fetcher)

        pages = await source.pages("7/100")

        self.assertEqual(fetcher.requests[1][1], f"{BASE}/api/capitulo/comic-el-gato/100")
        self.assertEqual([page.source_id for page in pages], ["https://cdn/1.jpg", "https://cdn/2.jpg"])

    async def test_el_dominio_se_autodescubre_cuando_esta_activo(self):
        fetcher = Fetcher([
            Response(_ := "https://olympus.pages.dev",
                     '<meta property="og:url" content="https://olympus.link/ir">'),
            Response("https://nuevodominio.com/", ""),
            Response("https://nuevodominio.com/api/series/list", LISTA),
            Response("https://nuevodominio.com/api/rankings",
                     {"data": [], "current_page": 1, "last_page": 1}),
        ])
        source = source_class(fetch_domain=True)(fetcher)

        await source.browse("popular")

        self.assertEqual(source.base_url, "https://nuevodominio.com")
        self.assertEqual(fetcher.requests[2][1], "https://nuevodominio.com/api/series/list")


if __name__ == "__main__":
    unittest.main()
