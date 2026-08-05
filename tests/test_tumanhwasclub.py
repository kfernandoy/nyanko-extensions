from __future__ import annotations

import unittest
from pathlib import Path

from tools.generate import _manual_bundle

BASE = "https://manhwas.me"


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
    path = Path(__file__).parents[1] / "engines" / "manual" / "tumanhwasclub_es.py"
    namespace = {"__name__": "test_tumanhwasclub_bundle"}
    exec(compile(_manual_bundle(path), str(path), "exec"), namespace)
    return namespace["SOURCE"]


RESULTADOS = """
<div class="results-grid">
  <a class="result-card" href="/manga/gato">
    <div class="result-card-image"><img data-src="/gato.jpg" src="/ph.jpg"></div>
    <div class="result-card-title">Gato</div></a>
</div>
<div class="pagination"><a class="page-btn" href="/search?page=3"><i class="fa-chevron-right"></i></a></div>
"""


class ManhwasMeTest(unittest.IsolatedAsyncioTestCase):
    async def test_catalogos_ordenan_por_vistas_y_actualizacion(self):
        fetcher = Fetcher([
            Response(f"{BASE}/search", RESULTADOS),
            Response(f"{BASE}/search", RESULTADOS),
        ])
        source = source_class()(fetcher)

        popular = await source.browse("popular", 2)
        await source.browse("latest", 1)

        self.assertEqual(fetcher.requests[0][2]["params"], [("sort", "-views"), ("page", "2")])
        self.assertEqual(fetcher.requests[1][2]["params"], [("sort", "-updated_at"), ("page", "1")])
        self.assertEqual(
            [(item.source_id, item.title, item.cover_url) for item in popular["items"]],
            [("manga/gato", "Gato", f"{BASE}/gato.jpg")],
        )
        self.assertTrue(popular["has_more"])

    async def test_busqueda_usa_filter_name_y_manda_todos_los_filtros(self):
        fetcher = Fetcher([Response(f"{BASE}/search", RESULTADOS)])
        source = source_class()(fetcher)

        await source.search("gato", 2, {"sort": "-views", "genre": "drama"})

        self.assertEqual(fetcher.requests[0][2]["params"], [
            ("page", "2"), ("filter[name]", "gato"),
            ("sort", "-views"), ("type", ""), ("genre", "drama"), ("status", ""), ("caution", ""),
        ])

    async def test_las_rutas_heredadas_de_tumanhwas_se_reescriben(self):
        ficha = '<h1 class="detail-title">Gato</h1>'
        fetcher = Fetcher([Response(f"{BASE}/manga/gato", ficha)])
        source = source_class()(fetcher)

        await source.details("manhwa/gato")

        self.assertEqual(fetcher.requests[0][1], f"{BASE}/manga/gato")

    async def test_ficha_lee_las_filas_de_estadisticas(self):
        ficha = """
        <h1 class="detail-title">Gato</h1>
        <div class="detail-hero-cover"><img data-src="/gato.jpg"></div>
        <div class="detail-stat-row"><span class="detail-stat-label">Autores</span>
          <span class="detail-stat-value">Kim</span></div>
        <div class="detail-stat-row"><span class="detail-stat-label">Géneros</span>
          <span class="detail-stat-value"><a>Acción</a><a>Drama</a></span></div>
        <div class="detail-synopsis"><p>Una historia.</p></div>
        <span class="detail-tag-year">En curso</span>
        """
        fetcher = Fetcher([Response(f"{BASE}/manga/gato", ficha)])
        source = source_class()(fetcher)

        manga = await source.details("manga/gato")

        self.assertEqual((manga.title, manga.author, manga.status), ("Gato", "Kim", "ongoing"))
        self.assertEqual((manga.description, manga.content_tags), ("Una historia.", ("Acción", "Drama")))

    async def test_capitulos_normalizan_el_titulo(self):
        ficha = """
        <div class="detail-chapter-row">
          <span class="detail-col-chapter"><a href="/manga/gato/90">Ch. 90.00</a></span>
          <span class="detail-col-updated">05/08/26</span></div>
        <div class="detail-chapter-row">
          <span class="detail-col-chapter"><a href="/manga/gato/89">Ch. 89</a></span>
          <span class="detail-col-updated">hace 2 días</span></div>
        """
        fetcher = Fetcher([Response(f"{BASE}/manga/gato", ficha)])
        source = source_class()(fetcher)

        chapters = await source.chapters("manga/gato")

        # "Ch. 90.00" queda como "Chapter 90".
        self.assertEqual(
            [(c.source_id, c.title, c.number) for c in chapters],
            [("manga/gato/90", "Chapter 90", 90.0), ("manga/gato/89", "Chapter 89", 89.0)],
        )
        self.assertEqual(chapters[0].uploaded_at, "2026-08-05T00:00:00")
        self.assertIsNotNone(chapters[1].uploaded_at)

    async def test_lector_prefiere_data_src(self):
        lector = """
        <div class="reader-pages">
          <div class="img-wrap"><img data-src="/p/1.jpg" src="/ph.jpg"></div>
          <div class="img-wrap"><img src="/p/2.jpg"></div>
        </div>
        """
        fetcher = Fetcher([Response(f"{BASE}/manga/gato/90", lector)])
        source = source_class()(fetcher)

        pages = await source.pages("manga/gato/90")

        self.assertEqual([page.source_id for page in pages], [f"{BASE}/p/1.jpg", f"{BASE}/p/2.jpg"])
        self.assertEqual(source.capabilities.content_warning, "nsfw")


if __name__ == "__main__":
    unittest.main()
