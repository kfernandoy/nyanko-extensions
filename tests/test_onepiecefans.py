from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.generate import _manual_bundle

BASE = "https://one-piece-fans2.com"


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


def source_class(extension_id: str = "onepiecefans_es"):
    path = Path(__file__).parents[1] / "engines" / "manual" / f"{extension_id}.py"
    namespace = {"__name__": f"test_{extension_id}_bundle"}
    exec(compile(_manual_bundle(path), str(path), "exec"), namespace)
    return namespace["SOURCE"]


CONFIG = {
    "es": [{"path": "opf", "title": "OPF"}, {"path": "otro", "title": "Otro"}],
    "en": [{"path": "eng", "title": "Eng"}],
}


class OnePieceFansTest(unittest.IsolatedAsyncioTestCase):
    async def test_el_catalogo_sale_del_json_de_fansubs_por_idioma(self):
        fetcher = Fetcher([Response(f"{BASE}/fansubs-config.json", CONFIG)])
        source = source_class()(fetcher)

        result = await source.browse("popular")

        self.assertEqual(fetcher.requests[0][1], f"{BASE}/fansubs-config.json")
        self.assertEqual(
            [(item.source_id, item.title) for item in result["items"]],
            [("opf", "One Piece (OPF)"), ("otro", "One Piece (Otro)")],
        )
        self.assertEqual(result["items"][0].cover_url, f"{BASE}/images/luffy.png")
        self.assertEqual(result["items"][0].web_url, f"{BASE}/manga/es/opf")
        self.assertFalse(result["has_more"])

    async def test_cada_idioma_lee_su_propia_clave(self):
        fetcher = Fetcher([Response(f"{BASE}/fansubs-config.json", CONFIG)])
        source = source_class("onepiecefans_en")(fetcher)

        result = await source.browse("popular")

        self.assertEqual([item.source_id for item in result["items"]], ["eng"])

    async def test_no_hay_recientes(self):
        source = source_class()(Fetcher([]))

        result = await source.browse("latest")

        self.assertEqual(result, {"items": [], "has_more": False})
        self.assertFalse(source.supports_latest)

    async def test_la_busqueda_reusa_el_catalogo_e_ignora_la_consulta(self):
        fetcher = Fetcher([Response(f"{BASE}/fansubs-config.json", CONFIG)])
        source = source_class()(fetcher)

        result = await source.search("cualquier cosa")

        self.assertEqual(fetcher.requests[0][1], f"{BASE}/fansubs-config.json")
        self.assertEqual([item.source_id for item in result["items"]], ["opf", "otro"])

    async def test_capitulos_y_paginas_arman_las_rutas_por_carpeta(self):
        fetcher = Fetcher([
            Response(f"{BASE}/server.php", ["1", "2"]),
            Response(f"{BASE}/server.php", ["01.jpg", "02.jpg"]),
        ])
        source = source_class()(fetcher)

        chapters = await source.chapters("opf")
        pages = await source.pages(chapters[1])

        self.assertEqual(fetcher.requests[0][2]["params"], {"lang": "es", "folderName": "opf"})
        self.assertEqual(
            [(c.source_id, c.title, c.number) for c in chapters],
            [("opf/1", "Chapter 1", 1.0), ("opf/2", "Chapter 2", 2.0)],
        )
        self.assertEqual(
            fetcher.requests[1][2]["params"],
            {"lang": "es", "folderName": "opf", "chapter": "2"},
        )
        self.assertEqual(
            [page.source_id for page in pages],
            [f"{BASE}/mangafiles/es/opf/2/01.jpg", f"{BASE}/mangafiles/es/opf/2/02.jpg"],
        )


if __name__ == "__main__":
    unittest.main()
