from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.generate import _manual_bundle

BASE = "https://onfmangas.com"


class Response:
    def __init__(self, url: str, text: str = "", content: bytes = b"", code: int = 200) -> None:
        self.url = url
        self.text = text
        self.content = content
        self.status_code = code
        self.headers = {"Content-Type": "image/jpeg"}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise ValueError(f"HTTP {self.status_code}")


class Fetcher:
    def __init__(self, responses: list[Response]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, str, dict]] = []

    async def request(self, method: str, url: str, **kwargs):
        self.requests.append((method, url, kwargs))
        return self.responses.pop(0)


def source_class():
    path = Path(__file__).parents[1] / "engines" / "manual" / "onfmangas_es.py"
    namespace = {"__name__": "test_onfmangas_bundle"}
    exec(compile(_manual_bundle(path), str(path), "exec"), namespace)
    return namespace["SOURCE"]


def hexed(name: str, payload) -> str:
    return f'<script>const {name} = "{json.dumps(payload).encode().hex()}";</script>'


class OnfMangasTest(unittest.IsolatedAsyncioTestCase):
    async def test_populares_leen_las_dos_clases_de_tarjeta(self):
        populares = """
        <a class="pop-podium-card" href="/manga/gato"><img src="/g.jpg">
          <div class="pop-podium-name">El Gato</div></a>
        <a class="pop-card" href="/manga/lobo"><img src="/l.jpg">
          <div class="pop-name">El Lobo</div></a>
        """
        fetcher = Fetcher([Response(f"{BASE}/populares.php", populares)])
        source = source_class()(fetcher)

        result = await source.browse("popular")

        self.assertEqual(
            [(item.source_id, item.title) for item in result["items"]],
            [("manga/gato", "El Gato"), ("manga/lobo", "El Lobo")],
        )
        self.assertFalse(result["has_more"])

    async def test_busqueda_usa_generos_indexados(self):
        grid = """
        <div class="manga-grid"><div class="manga-card">
          <a href="/manga/gato"></a><div class="manga-title">El Gato</div>
          <div class="card-cover"><img src="/g.jpg"></div></div></div>
        <div class="pagination"><a class="page-btn">Siguiente</a></div>
        """
        fetcher = Fetcher([
            Response(f"{BASE}/mangas.php", grid),
            Response(f"{BASE}/mangas.php", grid),
        ])
        source = source_class()(fetcher)

        result = await source.search("gato", 2, {"tab": "yuri", "genero": "14"})
        await source.search("", 1, {"genero": "0"})

        self.assertEqual(fetcher.requests[0][2]["params"], [
            ("q", "gato"), ("page", "2"), ("tab", "yuri"), ("generos[0]", "14"),
        ])
        # La categoria "0" no viaja.
        self.assertEqual(fetcher.requests[1][2]["params"], [
            ("q", ""), ("page", "1"), ("tab", "general"),
        ])
        self.assertTrue(result["has_more"])

    async def test_capitulos_se_decodifican_del_hexadecimal(self):
        payload = [
            {"url": "/leer/gato-1", "numero": "1", "fecha_subida": "2026-08-01 10:00:00",
             "grupos_list": [{"nombre": "Alfa"}]},
            {"url": "/leer/gato-2", "numero": "2", "titulo_str": "El giro",
             "fecha_subida": "2026-08-05 10:00:00",
             "otras_versiones": [{"url": "/leer/gato-2b", "grupos_list": [{"nombre": "Beta"}]}]},
        ]
        fetcher = Fetcher([Response(f"{BASE}/manga/gato", hexed("_hex", payload))])
        source = source_class()(fetcher)

        chapters = await source.chapters("manga/gato")

        # Orden descendente por numero; las otras versiones siguen a su capitulo.
        self.assertEqual(
            [(c.source_id, c.title, c.scanlator) for c in chapters],
            [
                ("leer/gato-2", "El giro", ""),
                ("leer/gato-2b", "El giro", "Beta"),
                ("leer/gato-1", "Capítulo 1", "Alfa"),
            ],
        )
        self.assertEqual(chapters[0].uploaded_at, "2026-08-05T10:00:00")
        # La version alterna hereda la fecha del capitulo padre.
        self.assertEqual(chapters[1].uploaded_at, "2026-08-05T10:00:00")

    async def test_el_lector_recurre_al_respaldo(self):
        payload = [
            {"src": "https://cdn/1.jpg", "fallback": "https://mirror/1.jpg"},
            {"src": "https://cdn/2.jpg", "fallback": None},
        ]
        fetcher = Fetcher([
            Response(f"{BASE}/leer/gato-2", hexed("_hexP", payload)),
            Response("https://cdn/1.jpg", code=502),
            Response("https://mirror/1.jpg", content=b"ok"),
        ])
        source = source_class()(fetcher)

        pages = await source.pages("leer/gato-2")
        content = await source.page_bytes(pages[0])

        self.assertEqual(pages[0].source_id, "https://cdn/1.jpg#fallback=https://mirror/1.jpg")
        self.assertEqual(pages[1].source_id, "https://cdn/2.jpg")
        self.assertEqual(fetcher.requests[2][1], "https://mirror/1.jpg")
        self.assertEqual(b"".join(content.chunks), b"ok")

    async def test_el_reto_de_verificacion_se_avisa(self):
        fetcher = Fetcher([Response(f"{BASE}/populares.php", "<h1>Verificando tu navegador</h1>")])
        source = source_class()(fetcher)

        with self.assertRaises(ValueError) as error:
            await source.browse("popular")

        self.assertIn("WebView", str(error.exception))


if __name__ == "__main__":
    unittest.main()
