from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.generate import _manual_bundle

API = "https://manta.net"


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


def source_class(extension_id: str = "manta_es"):
    path = Path(__file__).parents[1] / "engines" / "manual" / f"{extension_id}.py"
    namespace = {"__name__": f"test_{extension_id}_bundle"}
    exec(compile(_manual_bundle(path), str(path), "exec"), namespace)
    return namespace["SOURCE"]


LISTADO = {"data": [{
    "id": 7,
    "data": {"title": {"en": "Cat", "es": "Gato"}},
    "image": {"1280x1840_720": {"downloadUrl": "https://cdn/cover.jpg"}},
}]}

FICHA = {"data": {
    "id": 7,
    "image": {"1440x3072": {"downloadUrl": "https://cdn/big.jpg"}},
    "data": {
        "isCompleted": True,
        "description": {"short": "Corta.", "long": "Larga."},
        "tags": [{"name": {"en": "Romance", "es": "Romance"}}],
        "creators": [
            {"name": "Kim", "role": "Story"},
            {"name": "Lee", "role": "Illustration"},
        ],
    },
    "episodes": [
        {"id": 1, "ord": 1, "createdAt": "2026-08-01T10:00:00.123Z"},
        {"id": 2, "ord": 2, "data": {"title": "Un titulo"}, "openAt": "2026-08-05T12:00:00Z",
         "lockData": {"state": 130}},
        {"id": 3, "ord": 3, "lockData": {"state": 200}},
    ],
}}


class MantaTest(unittest.IsolatedAsyncioTestCase):
    async def test_populares_piden_la_categoria_new(self):
        fetcher = Fetcher([Response(f"{API}/manta/v1/search/series", LISTADO)])
        source = source_class()(fetcher)

        result = await source.browse("popular")

        self.assertEqual(fetcher.requests[0][1], f"{API}/manta/v1/search/series")
        self.assertEqual(fetcher.requests[0][2]["params"], [("cat", "New"), ("lang", "es")])
        self.assertEqual(
            [(item.source_id, item.title, item.cover_url) for item in result["items"]],
            [("7", "Gato", "https://cdn/cover.jpg")],
        )
        self.assertFalse(result["has_more"])

    async def test_el_titulo_sigue_al_idioma(self):
        fetcher = Fetcher([Response(f"{API}/manta/v1/search/series", LISTADO)])
        source = source_class("manta_en")(fetcher)

        result = await source.browse("popular")

        self.assertEqual(result["items"][0].title, "Cat")

    async def test_sin_consulta_viaja_la_categoria_por_defecto(self):
        fetcher = Fetcher([
            Response(f"{API}/manta/v1/search/series", LISTADO),
            Response(f"{API}/manta/v1/search/series", LISTADO),
            Response(f"{API}/manta/v1/search/series", LISTADO),
        ])
        source = source_class()(fetcher)

        await source.search("gato")
        await source.search("")
        await source.search("", 1, {"category": "tagId=16"})

        self.assertEqual(fetcher.requests[0][2]["params"], [("lang", "es"), ("q", "gato")])
        self.assertEqual(fetcher.requests[1][2]["params"], [("lang", "es"), ("tagId", "288")])
        self.assertEqual(fetcher.requests[2][2]["params"], [("lang", "es"), ("tagId", "16")])

    async def test_ficha_reparte_autores_e_ilustradores(self):
        fetcher = Fetcher([Response(f"{API}/front/v1/series/7", FICHA)])
        source = source_class()(fetcher)

        manga = await source.details("7")

        self.assertEqual(fetcher.requests[0][2]["params"], {"lang": "es"})
        self.assertEqual((manga.author, manga.artist), ("Kim", "Lee"))
        self.assertEqual((manga.status, manga.content_tags), ("completed", ("Romance",)))
        self.assertEqual(manga.description, "Corta.\n\nLarga.")

    async def test_capitulos_descartan_los_bloqueados_y_se_invierten(self):
        fetcher = Fetcher([Response(f"{API}/front/v1/series/7", FICHA)])
        source = source_class()(fetcher)

        chapters = await source.chapters("7")

        # state 200 esta bloqueado; 130 no. El orden se invierte.
        self.assertEqual(
            [(c.source_id, c.title, c.number) for c in chapters],
            [("2", "Un titulo", 2.0), ("1", "Episodio 1", 1.0)],
        )
        self.assertEqual(chapters[0].uploaded_at, "2026-08-05T12:00:00")
        self.assertEqual(chapters[1].uploaded_at, "2026-08-01T10:00:00")

    async def test_paginas_y_cabeceras(self):
        payload = {"data": {"cutImages": [
            {"downloadUrl": "https://cdn/1.jpg"}, {"downloadUrl": "https://cdn/2.jpg"},
        ]}}
        fetcher = Fetcher([Response(f"{API}/front/v1/episodes/2", payload)])
        source = source_class()(fetcher)

        pages = await source.pages("2")

        self.assertEqual(fetcher.requests[0][1], f"{API}/front/v1/episodes/2")
        self.assertEqual([page.source_id for page in pages], ["https://cdn/1.jpg", "https://cdn/2.jpg"])
        self.assertEqual(source.capabilities.headers["Origin"], API)
        self.assertEqual(source.capabilities.headers["Accept-Language"], "es")
        self.assertEqual(source.image_headers["Origin"], API)


if __name__ == "__main__":
    unittest.main()
