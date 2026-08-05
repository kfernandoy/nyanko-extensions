from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.generate import _manual_bundle

BASE = "https://www.neomanga.online"


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
    path = Path(__file__).parents[1] / "engines" / "manual" / "neomanga_es.py"
    namespace = {"__name__": "test_neomanga_bundle"}
    exec(compile(_manual_bundle(path), str(path), "exec"), namespace)
    return namespace["SOURCE"]


def flight(mangas: list[dict]) -> str:
    """Imita el payload RSC: JSON embebido entre ruido de Next.js."""
    return '3:["$","div",null,' + json.dumps({"initialMangas": mangas}) + "]\n"


CATALOGO = [
    {"title": "El Gato", "slug": "el-gato", "synopsis": "Una historia.",
     "cover_image_url": "https://cdn/gato.jpg", "status": "en_emision",
     "genres": ["Acción", "Romance"]},
    {"title": "El Lobo", "slug": "el-lobo", "cover_image_url": "/covers/lobo.jpg",
     "status": "finalizado", "genres": ["Drama"]},
]


class NeoMangaTest(unittest.IsolatedAsyncioTestCase):
    async def test_el_catalogo_sale_del_payload_rsc(self):
        fetcher = Fetcher([Response(f"{BASE}/series", flight(CATALOGO))])
        source = source_class()(fetcher)

        result = await source.browse("popular")

        self.assertEqual(fetcher.requests[0][2]["headers"], {"RSC": "1"})
        self.assertEqual([item.source_id for item in result["items"]], ["el-gato", "el-lobo"])
        self.assertEqual((result["items"][0].status, result["items"][1].status), ("ongoing", "completed"))
        # Las portadas externas pasan por el proxy de imagenes de Next.
        self.assertEqual(
            result["items"][0].cover_url,
            f"{BASE}/_next/image?url=https%3A%2F%2Fcdn%2Fgato.jpg&w=640&q=75",
        )
        # Las relativas se dejan tal cual.
        self.assertEqual(result["items"][1].cover_url, "/covers/lobo.jpg")
        self.assertFalse(result["has_more"])

    async def test_no_hay_recientes(self):
        source = source_class()(Fetcher([]))

        self.assertEqual(await source.browse("latest"), {"items": [], "has_more": False})

    async def test_la_busqueda_filtra_en_el_cliente(self):
        fetcher = Fetcher([
            Response(f"{BASE}/series", flight(CATALOGO)),
            Response(f"{BASE}/series", flight(CATALOGO)),
            Response(f"{BASE}/series", flight(CATALOGO)),
        ])
        source = source_class()(fetcher)

        texto = await source.search("lobo")
        estado = await source.search("", 1, {"status": "en_emision"})
        genero = await source.search("", 1, {"genre": "Drama"})

        self.assertEqual([item.source_id for item in texto["items"]], ["el-lobo"])
        self.assertEqual([item.source_id for item in estado["items"]], ["el-gato"])
        self.assertEqual([item.source_id for item in genero["items"]], ["el-lobo"])

    async def test_ficha_se_lee_del_html(self):
        ficha = """
        <h1>El Gato</h1>
        <p class="whitespace-pre-line">Una historia.</p>
        <div class="aspect-[3/4]"><img src="/_next/image?url=x&w=640&q=75"></div>
        <span class="bg-success">En emisión</span>
        <span class="bg-accent-soft">Acción</span><span class="bg-accent-soft">Romance</span>
        """
        fetcher = Fetcher([Response(f"{BASE}/manga/el-gato", ficha)])
        source = source_class()(fetcher)

        manga = await source.details("el-gato")

        self.assertEqual((manga.title, manga.description), ("El Gato", "Una historia."))
        self.assertEqual((manga.status, manga.content_tags), ("ongoing", ("Acción", "Romance")))

    async def test_capitulos_ordenan_por_numero(self):
        payload = '1:' + json.dumps({"chapters": [
            {"chapter_number": 1.0, "title": None, "published_at": "2026-08-01T10:00:00"},
            {"chapter_number": 2.5, "title": "El giro", "published_at": "2026-08-05T10:00:00"},
        ]})
        fetcher = Fetcher([Response(f"{BASE}/manga/el-gato", payload)])
        source = source_class()(fetcher)

        chapters = await source.chapters("el-gato")

        self.assertEqual(fetcher.requests[0][2]["headers"], {"RSC": "1"})
        self.assertEqual(
            [(c.source_id, c.title, c.number) for c in chapters],
            [
                ("manga/el-gato/capitulo/2.5", "El giro", 2.5),
                ("manga/el-gato/capitulo/1", "Capítulo 1", 1.0),
            ],
        )
        self.assertEqual(chapters[1].uploaded_at, "2026-08-01T10:00:00")

    async def test_las_paginas_de_mangadex_pasan_por_el_proxy(self):
        payload = '1:' + json.dumps({"chapter": {"pages_urls": [
            "https://cdn/1.jpg", "MANGADEX:xyz",
        ]}})
        fetcher = Fetcher([
            Response(f"{BASE}/manga/el-gato/capitulo/1", payload),
            Response(f"{BASE}/api/mangadex-pages/xyz", {"pages": ["a", "b"]}),
        ])
        source = source_class()(fetcher)

        pages = await source.pages("manga/el-gato/capitulo/1")

        self.assertEqual(fetcher.requests[1][1], f"{BASE}/api/mangadex-pages/xyz")
        self.assertEqual([page.source_id for page in pages], [
            "https://cdn/1.jpg",
            f"{BASE}/api/manga-page/xyz/0",
            f"{BASE}/api/manga-page/xyz/1",
        ])


if __name__ == "__main__":
    unittest.main()
