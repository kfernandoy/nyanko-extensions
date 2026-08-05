from __future__ import annotations

import unittest
from pathlib import Path

from tools.generate import _manual_bundle

BASE = "https://rncalation.online"


class Response:
    def __init__(self, url: str, text: str, headers: dict | None = None) -> None:
        self.url = url
        self.text = text
        self.headers = headers or {}
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
    path = Path(__file__).parents[1] / "engines" / "manual" / "nartag_es.py"
    namespace = {"__name__": "test_nartag_bundle"}
    exec(compile(_manual_bundle(path), str(path), "exec"), namespace)
    return namespace["SOURCE"]


BIBLIOTECA = """
<div class="lib-grid">
  <a class="comic-card" href="/comics/gato"><img src="/gato.jpg">
    <p class="leading-snug">Gato</p></a>
  <a class="comic-card" href="/comics/novela">
    <span class="absolute top-2 left-2">Novel</span>
    <img src="/n.jpg"><p class="leading-snug">Novela</p></a>
</div>
<div><a class="lib-page-btn--nav" href="/library?page=3">Siguiente</a></div>
"""


class NartagTest(unittest.IsolatedAsyncioTestCase):
    async def test_biblioteca_descarta_novelas_y_detecta_siguiente(self):
        fetcher = Fetcher([Response(f"{BASE}/library", BIBLIOTECA)])
        source = source_class()(fetcher)

        result = await source.browse("popular", 2)

        self.assertEqual(fetcher.requests[0][2]["params"], [("sort", "views"), ("page", "2")])
        self.assertEqual(
            [(item.source_id, item.title) for item in result["items"]],
            [("comics/gato", "Gato")],
        )
        self.assertTrue(result["has_more"])

    async def test_recientes_ordenan_por_updated(self):
        fetcher = Fetcher([Response(f"{BASE}/library", BIBLIOTECA)])
        source = source_class()(fetcher)

        await source.browse("latest", 1)

        self.assertEqual(fetcher.requests[0][2]["params"], [("sort", "updated"), ("page", "1")])

    async def test_busqueda_omite_los_filtros_en_todos(self):
        fetcher = Fetcher([Response(f"{BASE}/library", BIBLIOTECA)])
        source = source_class()(fetcher)

        await source.search("gato", 2, {"sort": "rating", "type": "Manhwa", "status": "", "genre": "Acción"})

        self.assertEqual(fetcher.requests[0][2]["params"], [
            ("page", "2"), ("q", "gato"), ("sort", "rating"),
            ("type", "Manhwa"), ("genre", "Acción"),
        ])

    async def test_ficha_separa_estado_de_generos(self):
        ficha = """
        <div class="comic-page-wrap"><p class="text-sm">Una historia.</p></div>
        <span class="inline-flex items-center rounded">Emisión</span>
        <span class="inline-flex items-center rounded">Acción</span>
        <a href="/groups/equipo">Equipo</a>
        <div class="flex items-baseline justify-between gap-2">
          <span class="text-[var(--color-text3)]">Autor</span>
          <span class="text-[var(--color-text2)]">Kim</span>
        </div>
        <div class="flex items-baseline justify-between gap-2">
          <span class="text-[var(--color-text3)]">Arte</span>
          <span class="text-[var(--color-text2)]">Lee</span>
        </div>
        """
        fetcher = Fetcher([Response(f"{BASE}/comics/gato", ficha)])
        source = source_class()(fetcher)

        manga = await source.details("comics/gato")

        self.assertEqual((manga.status, manga.description), ("ongoing", "Una historia."))
        # El badge de estado no cuenta como genero.
        self.assertEqual(manga.content_tags, ("Acción",))
        self.assertEqual((manga.author, manga.artist), ("Kim", "Lee"))

    async def test_capitulos_paginan_por_cabeceras(self):
        primera = """
        <a data-chapter-id="1" data-chapter-num="2" data-chapter-label="Capítulo 2"
           href="/comics/gato/2"><span class="text-[0.65rem]">hace 3 días</span></a>
        """
        segunda = """
        <a data-chapter-id="2" data-chapter-num="1" data-chapter-label=""
           href="/comics/gato/1"><span class="text-[0.65rem]">Aug 5, 2026</span></a>
        """
        fetcher = Fetcher([
            Response(f"{BASE}/comics/gato/chapters", primera, {"x-page": "1", "x-pages": "2"}),
            Response(f"{BASE}/comics/gato/chapters", segunda, {"x-page": "2", "x-pages": "2"}),
        ])
        source = source_class()(fetcher)

        chapters = await source.chapters("comics/gato")

        self.assertEqual(fetcher.requests[0][1], f"{BASE}/comics/gato/chapters")
        self.assertEqual(fetcher.requests[1][2]["params"], {"page": "2"})
        self.assertEqual(
            [(c.source_id, c.title, c.number) for c in chapters],
            [("comics/gato/2", "Capítulo 2", 2.0), ("comics/gato/1", "Capítulo 1", 1.0)],
        )
        # Etiqueta vacia: se compone desde el numero, como el Kotlin.
        self.assertEqual(chapters[1].uploaded_at, "2026-08-05T00:00:00")

    async def test_lector_prefiere_data_src(self):
        lector = """
        <img class="page-img" data-src="/p/1.jpg" src="/placeholder.jpg">
        <div class="page-wrap"><img src="/p/2.jpg"></div>
        """
        fetcher = Fetcher([Response(f"{BASE}/comics/gato/2", lector)])
        source = source_class()(fetcher)

        pages = await source.pages("comics/gato/2")

        self.assertEqual([page.source_id for page in pages], [f"{BASE}/p/1.jpg", f"{BASE}/p/2.jpg"])
        self.assertEqual(source.capabilities.requests_per_minute, 120)


if __name__ == "__main__":
    unittest.main()
