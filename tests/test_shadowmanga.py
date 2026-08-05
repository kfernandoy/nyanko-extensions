from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.generate import _manual_bundle

BASE = "https://shademanga.com"


class Response:
    def __init__(self, url: str, payload=None, content: bytes = b"", code: int = 200) -> None:
        self.url = url
        self.text = json.dumps(payload if payload is not None else {})
        self.content = content
        self.status_code = code
        self.headers = {"Content-Type": "image/jpeg"}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise ValueError(f"HTTP {self.status_code}")

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
    path = Path(__file__).parents[1] / "engines" / "manual" / "shadowmanga_es.py"
    namespace = {"__name__": "test_shadowmanga_bundle"}
    exec(compile(_manual_bundle(path), str(path), "exec"), namespace)
    return namespace["SOURCE"]


SERIE = {
    "id": 7, "titulo": "El Gato", "portadaUrl": "https://media.shademanga.com/g.jpg",
    "descripcion": "Una historia.", "autor": "Kim", "generos": "Acción, Romance",
    "estado": "En curso",
}


class ShadowMangaTest(unittest.IsolatedAsyncioTestCase):
    async def test_populares_aplanan_los_grupos_y_deduplican(self):
        payload = [
            {"series": [SERIE, {"id": 8, "titulo": "El Lobo"}]},
            {"series": [SERIE]},
        ]
        fetcher = Fetcher([Response(f"{BASE}/api/series-locales/popular", payload)])
        source = source_class()(fetcher)

        result = await source.browse("popular")

        self.assertEqual(fetcher.requests[0][1], f"{BASE}/api/series-locales/popular")
        self.assertEqual([item.source_id for item in result["items"]], ["7", "8"])
        self.assertFalse(result["has_more"])

    async def test_busqueda_incluye_y_excluye_generos(self):
        payload = [
            SERIE,
            {"id": 8, "titulo": "A Lobo", "generos": "Drama"},
        ]
        fetcher = Fetcher([Response(f"{BASE}/api/series-locales/search-candidates", payload)])
        source = source_class()(fetcher)

        result = await source.search("gato", 1, {"tags": {"Acción": "include", "Drama": "exclude"}})

        self.assertEqual(fetcher.requests[0][2]["params"], [
            ("q", "gato"), ("includeAdult", "true"), ("showSinPortada", "false"),
            ("take", "120"), ("tags", "Acción"),
        ])
        # "A Lobo" iria primero al ordenar por titulo, pero queda excluido por Drama.
        self.assertEqual([item.source_id for item in result["items"]], ["7"])

    async def test_los_generos_se_piden_una_vez(self):
        fetcher = Fetcher([Response(f"{BASE}/api/series-locales/tags", ["Acción", "Drama"])])
        source = source_class()(fetcher)

        primeros = await source.get_filters()
        await source.get_filters()

        self.assertEqual(len(fetcher.requests), 1)
        self.assertEqual(primeros[0].id, "tags")
        self.assertEqual(primeros[0].options, [("Acción", "Acción"), ("Drama", "Drama")])

    async def test_ficha_y_capitulos_ordenados(self):
        ficha = {**SERIE, "capitulos": [
            {"id": 10, "numeroCapitulo": 1.0, "titulo": None,
             "fechaSubida": "2026-08-01T10:00:00.000000"},
            {"id": 11, "numeroCapitulo": 2.5, "titulo": "El giro",
             "fechaSubida": "2026-08-05T10:00:00.000000"},
        ]}
        fetcher = Fetcher([
            Response(f"{BASE}/api/series-locales/7", ficha),
            Response(f"{BASE}/api/series-locales/7", ficha),
        ])
        source = source_class()(fetcher)

        manga = await source.details("7")
        chapters = await source.chapters("7")

        self.assertEqual((manga.author, manga.status), ("Kim", "ongoing"))
        self.assertEqual(manga.content_tags, ("Acción", "Romance"))
        self.assertEqual(
            [(c.source_id, c.title, c.number) for c in chapters],
            [("7/11", "Cap. 2.5 - El giro", 2.5), ("7/10", "Cap. 1", 1.0)],
        )
        self.assertEqual(chapters[0].uploaded_at, "2026-08-05T10:00:00")

    async def test_la_imagen_recorre_los_cdn_y_el_respaldo(self):
        paginas = {"paginas": ["https://media.shademanga.com/api/media/x/1.jpg"]}
        fetcher = Fetcher([
            Response(f"{BASE}/api/series-locales/7/capitulos/11/paginas", paginas),
            Response("https://media.shademanga.com/api/media/x/1.jpg", code=502),
            Response("https://cdn.shademanga.com/api/media/x/1.jpg", code=502),
            Response(f"{BASE}/api/media/x/1.jpg", content=b"ok"),
        ])
        source = source_class()(fetcher)

        pages = await source.pages("7/11")
        content = await source.page_bytes(pages[0])

        self.assertEqual(
            [request[1] for request in fetcher.requests[1:]],
            [
                "https://media.shademanga.com/api/media/x/1.jpg",
                "https://cdn.shademanga.com/api/media/x/1.jpg",
                f"{BASE}/api/media/x/1.jpg",
            ],
        )
        self.assertEqual(b"".join(content.chunks), b"ok")


if __name__ == "__main__":
    unittest.main()
