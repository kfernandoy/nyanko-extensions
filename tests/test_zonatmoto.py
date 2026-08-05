from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.generate import _manual_bundle

BASE = "https://zonatmo.to"
API = f"{BASE}/wp-api/api"
CDN = "https://cdn.zonatmo.to"


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
    path = Path(__file__).parents[1] / "engines" / "manual" / "zonatmoto_es.py"
    namespace = {"__name__": "test_zonatmoto_bundle"}
    exec(compile(_manual_bundle(path), str(path), "exec"), namespace)
    return namespace["SOURCE"]


SERIE = {
    "slug": "el-gato", "title": "El Gato", "overview": "Una historia.",
    "cover": "2026/08/gato.jpg", "author": [{"name": "Kim"}, {"name": "Kim"}],
    "status": [19], "genres": [2, 16],
}


class ZonatmoToTest(unittest.IsolatedAsyncioTestCase):
    async def test_populares_usan_el_top_mensual(self):
        fetcher = Fetcher([
            Response(f"{API}/tops/views/month", {"data": {"items": [SERIE, {"slug": "", "title": "x"}]}}),
        ])
        source = source_class()(fetcher)

        result = await source.browse("popular")

        self.assertEqual(fetcher.requests[0][2]["params"], [("postType", "any"), ("postsPerPage", "50")])
        self.assertEqual(fetcher.requests[0][2]["headers"]["Referer"], f"{BASE}/")
        # La entrada sin slug se descarta.
        self.assertEqual([item.source_id for item in result["items"]], ["el-gato"])
        item = result["items"][0]
        self.assertEqual(item.cover_url, f"{BASE}/wp-content/uploads/2026/08/gato.jpg")
        # Los autores repetidos se colapsan y los generos se resuelven por id.
        self.assertEqual((item.author, item.status), ("Kim", "completed"))
        self.assertEqual(item.content_tags, ("Acción", "Romance"))
        self.assertFalse(result["has_more"])

    async def test_no_hay_recientes(self):
        source = source_class()(Fetcher([]))

        self.assertEqual(await source.browse("latest"), {"items": [], "has_more": False})

    async def test_busqueda_repite_los_filtros_multiples(self):
        payload = {"data": {"items": [SERIE], "pagination": {"has_next": True, "total_pages": 3}}}
        fetcher = Fetcher([Response(f"{API}/listing/manga", payload)])
        source = source_class()(fetcher)

        result = await source.search("gato", 2, {
            "genres": ["2", "16"], "type": ["14"], "status": ["12"],
        })

        self.assertEqual(fetcher.requests[0][2]["params"], [
            ("page", "2"), ("search", "gato"),
            ("genres[]", "2"), ("genres[]", "16"), ("type[]", "14"), ("status[]", "12"),
        ])
        self.assertTrue(result["has_more"])

    async def test_pegar_una_url_del_sitio_abre_la_ficha(self):
        fetcher = Fetcher([Response(f"{API}/single/manga/el-gato", {"data": SERIE})])
        source = source_class()(fetcher)

        result = await source.search("https://zonatmo.to/manga/el-gato/algo")

        self.assertEqual(fetcher.requests[0][1], f"{API}/single/manga/el-gato")
        self.assertEqual([item.source_id for item in result["items"]], ["el-gato"])

    async def test_capitulos_recorren_todas_las_paginas_y_ordenan(self):
        primera = {"data": {
            "items": [{"id": 1, "chapter_number": "1", "title": "", "slug": "cap-1",
                       "release_date": "2026-08-01 10:00:00"}],
            "pagination": {"has_next": True, "total_pages": 2},
        }}
        segunda = {"data": {
            "items": [{"id": 2, "chapter_number": "2.5", "title": "El giro", "slug": "cap-2-5",
                       "release_date": "2026-08-05 10:00:00"}],
            "pagination": {"has_next": False, "total_pages": 2},
        }}
        fetcher = Fetcher([
            Response(f"{API}/single/manga/el-gato/chapters", primera),
            Response(f"{API}/single/manga/el-gato/chapters", segunda),
        ])
        source = source_class()(fetcher)

        chapters = await source.chapters("el-gato")

        self.assertEqual(fetcher.requests[0][2]["params"], [
            ("page", "1"), ("postsPerPage", "50"), ("order", "asc"),
        ])
        self.assertEqual(fetcher.requests[1][2]["params"][0], ("page", "2"))
        self.assertEqual(
            [(c.source_id, c.title, c.number) for c in chapters],
            [("el-gato/cap-2-5#2", "#2.5 - El giro", 2.5), ("el-gato/cap-1#1", "#1", 1.0)],
        )
        self.assertEqual(chapters[0].uploaded_at, "2026-08-05T10:00:00")

    async def test_las_paginas_se_ordenan_y_van_al_cdn(self):
        payload = {"data": {"chapter": {"jit": "abc/def", "images": [
            {"image_url": "2.jpg", "page_number": 2},
            {"image_url": "1.jpg", "page_number": 1},
        ]}}}
        fetcher = Fetcher([Response(f"{API}/single/manga/el-gato/cap-2-5", payload)])
        source = source_class()(fetcher)

        pages = await source.pages("el-gato/cap-2-5#2")

        self.assertEqual(fetcher.requests[0][1], f"{API}/single/manga/el-gato/cap-2-5")
        self.assertEqual(
            [page.source_id for page in pages],
            [f"{CDN}/manga/abc/def/1.jpg", f"{CDN}/manga/abc/def/2.jpg"],
        )


if __name__ == "__main__":
    unittest.main()
