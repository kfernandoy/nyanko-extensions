from __future__ import annotations

import base64
import unittest
from pathlib import Path

from tools.generate import _manual_bundle

BASE = "https://lector.ragnascan.xyz"


class Response:
    def __init__(self, url: str, text: str) -> None:
        self.url = url
        self.text = text
        self.status_code = 200

    def raise_for_status(self) -> None:
        pass


class Fetcher:
    def __init__(self, responses: list[Response]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, str, dict]] = []

    async def request(self, method: str, url: str, **kwargs):
        self.requests.append((method, url, kwargs))
        return self.responses.pop(0)


def source_class():
    path = Path(__file__).parents[1] / "engines" / "manual" / "ragnascans_es.py"
    namespace = {"__name__": "test_ragnascans_bundle"}
    exec(compile(_manual_bundle(path), str(path), "exec"), namespace)
    return namespace["SOURCE"]


def verify(url: str) -> str:
    """El sitio guarda la URL invertida y luego codificada en base64."""
    return base64.b64encode(url[::-1].encode()).decode()


DIRECTORIO = """
<div class="mod-grid">
  <a class="mod-card" href="/serie/gato"><img class="mod-card-cover" src="/gato.jpg">
    <span class="mod-card-title">Gato</span></a>
</div>
<a class="mod-pg-btn" href="/directorio.php?page=3">Sig &raquo;</a>
"""


class RagnaScansTest(unittest.IsolatedAsyncioTestCase):
    async def test_directorio_ordena_por_vistas_y_actualizado(self):
        fetcher = Fetcher([
            Response(f"{BASE}/directorio.php", DIRECTORIO),
            Response(f"{BASE}/directorio.php", DIRECTORIO),
        ])
        source = source_class()(fetcher)

        popular = await source.browse("popular", 2)
        await source.browse("latest", 1)

        self.assertEqual(fetcher.requests[0][2]["params"], [("page", "2"), ("orden", "vistas"), ("q", "")])
        self.assertEqual(fetcher.requests[1][2]["params"], [("page", "1"), ("orden", "actualizado"), ("q", "")])
        self.assertEqual(
            [(item.source_id, item.title, item.cover_url) for item in popular["items"]],
            [("serie/gato", "Gato", f"{BASE}/gato.jpg")],
        )
        self.assertTrue(popular["has_more"])

    async def test_busqueda_repite_los_filtros_multiples(self):
        fetcher = Fetcher([Response(f"{BASE}/directorio.php", DIRECTORIO)])
        source = source_class()(fetcher)

        await source.search("gato", 1, {
            "generos": ["Acción", "Drama"], "estado": ["emision"], "tipo": ["manhwa"], "orden": "az",
        })

        self.assertEqual(fetcher.requests[0][2]["params"], [
            ("page", "1"), ("q", "gato"),
            ("generos[]", "Acción"), ("generos[]", "Drama"),
            ("estado[]", "emision"), ("tipo[]", "manhwa"),
            ("orden", "az"),
        ])

    async def test_pegar_una_url_del_sitio_abre_la_ficha(self):
        ficha = '<h1>Gato</h1><div id="sinopsisWrapper"><p>Una historia.</p></div>'
        fetcher = Fetcher([Response(f"{BASE}/serie/gato", ficha)])
        source = source_class()(fetcher)

        result = await source.search(f"{BASE}/serie/gato")

        self.assertEqual(fetcher.requests[0][1], f"{BASE}/serie/gato")
        self.assertEqual([item.source_id for item in result["items"]], ["serie/gato"])
        self.assertFalse(result["has_more"])

    async def test_ficha_lee_la_tabla_de_metadatos(self):
        ficha = """
        <h1>Gato</h1>
        <div class="cover-wrapper"><img src="/gato.jpg"></div>
        <div class="flex flex-wrap items-center gap-x-3">
          <span>Autor: Kim</span><span>Ilustrador: Lee</span>
        </div>
        <div id="sinopsisWrapper"><p>Una historia.</p></div>
        <div class="meta-table">
          <div class="meta-row"><span class="meta-label">Género</span>
            <div class="meta-value"><a>Acción</a><a>Drama</a></div></div>
          <div class="meta-row"><span class="meta-label">Estado</span>
            <div class="meta-value">Finalizado</div></div>
        </div>
        """
        fetcher = Fetcher([Response(f"{BASE}/serie/gato", ficha)])
        source = source_class()(fetcher)

        manga = await source.details("serie/gato")

        self.assertEqual((manga.title, manga.author, manga.artist), ("Gato", "Kim", "Lee"))
        self.assertEqual((manga.status, manga.description), ("completed", "Una historia."))
        self.assertEqual(manga.content_tags, ("Acción", "Drama"))

    async def test_capitulos_descartan_los_bloqueados(self):
        ficha = """
        <div id="chaptersContainer">
          <a class="chapter-item" href="/leer/gato-2">
            <div class="chapter-item-title"><h4>Capítulo 2.00</h4></div>
            <span class="chapter-item-date">05 agosto, 2026</span></a>
          <a class="chapter-item locked-neon" href="/leer/gato-3">
            <div class="chapter-item-title"><h4>Capítulo 3</h4></div></a>
          <a class="chapter-item" href="/leer/gato-4">
            <div class="chapter-item-title"><h4>Capítulo 4</h4></div>
            <i class="ph-lock-key"></i></a>
        </div>
        """
        fetcher = Fetcher([Response(f"{BASE}/serie/gato", ficha)])
        source = source_class()(fetcher)

        chapters = await source.chapters("serie/gato")

        # El sufijo .00 se recorta y los de pago no aparecen.
        self.assertEqual(
            [(c.source_id, c.title, c.number, c.uploaded_at) for c in chapters],
            [("leer/gato-2", "Capítulo 2", 2.0, "2026-08-05T00:00:00")],
        )

    async def test_lector_decodifica_data_verify(self):
        lector = f"""
        <div id="pagesContainer">
          <div class="page-container"><img data-verify="{verify('/p/1.jpg')}"></div>
          <div class="page-container"><img data-verify="{verify('https://cdn/2.jpg')}"></div>
          <div class="page-container"><img data-verify="{verify('//cdn/3.jpg')}"></div>
          <div class="page-container"><img src="/p/4.jpg"></div>
          <div class="page-container"><img src="data:image/gif;base64,AA"></div>
        </div>
        """
        fetcher = Fetcher([Response(f"{BASE}/leer/gato-2", lector)])
        source = source_class()(fetcher)

        pages = await source.pages("leer/gato-2")

        self.assertEqual(
            [page.source_id for page in pages],
            [f"{BASE}/p/1.jpg", "https://cdn/2.jpg", "https://cdn/3.jpg", f"{BASE}/p/4.jpg"],
        )
        self.assertEqual(source.capabilities.requests_per_minute, 60)


if __name__ == "__main__":
    unittest.main()
