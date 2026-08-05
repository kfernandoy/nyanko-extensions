from __future__ import annotations

import unittest
from pathlib import Path

from tools.generate import _manual_bundle

BASE = "https://falcoscan.net"


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
    path = Path(__file__).parents[1] / "engines" / "manual" / "tenkaiscan_es.py"
    namespace = {"__name__": "test_tenkaiscan_bundle"}
    exec(compile(_manual_bundle(path), str(path), "exec"), namespace)
    return namespace["SOURCE"]


class FalcoScanTest(unittest.IsolatedAsyncioTestCase):
    async def test_ranking_lee_la_url_del_onclick(self):
        ranking = """
        <section class="trending"><div class="row">
          <div class="card" onclick="window.location.href='/comic/gato'">
            <img data-src="/gato.jpg" src="/ph.jpg">
            <div class="name"><h4 class="color-white">Gato</h4></div></div>
        </div></section>
        """
        fetcher = Fetcher([Response(f"{BASE}/ranking", ranking)])
        source = source_class()(fetcher)

        result = await source.browse("popular")

        self.assertEqual(fetcher.requests[0][1], f"{BASE}/ranking")
        self.assertEqual([(item.source_id, item.title) for item in result["items"]], [("comic/gato", "Gato")])
        # data-src gana sobre src.
        self.assertEqual(result["items"][0].cover_url, f"{BASE}/gato.jpg")
        self.assertFalse(result["has_more"])

    async def test_recientes_usan_anclas_de_la_portada(self):
        portada = """
        <section class="trending"><div class="row">
          <a href="/comic/lobo"><img src="/lobo.jpg">
            <div class="content"><h4 class="color-white">Lobo</h4></div></a>
        </div></section>
        """
        fetcher = Fetcher([Response(BASE, portada)])
        source = source_class()(fetcher)

        result = await source.browse("latest")

        self.assertEqual(fetcher.requests[0][1], BASE)
        self.assertEqual([item.source_id for item in result["items"]], ["comic/lobo"])

    async def test_busqueda_por_texto_y_filtro_unico(self):
        listado = """
        <section class="trending"><div class="row"><div class="col-xxl-9"><div class="row"><div>
          <a href="/comic/oso"><img src="/oso.jpg">
            <div class="content"><h4 class="color-white">Oso</h4></div></a>
        </div></div></div></div></section>
        """
        fetcher = Fetcher([
            Response(f"{BASE}/comics", listado),
            Response(f"{BASE}/comics", listado),
        ])
        source = source_class()(fetcher)

        texto = await source.search("oso", 1, {"gen": "Drama"})
        await source.search("", 1, {"gen": "Drama", "status": "Canceled"})

        # Con texto los filtros no viajan.
        self.assertEqual(fetcher.requests[0][2]["params"], [("search", "oso")])
        self.assertEqual([item.source_id for item in texto["items"]], ["comic/oso"])
        self.assertEqual(fetcher.requests[1][2]["params"], [("gen", "Drama"), ("status", "Canceled")])

    async def test_status_en_su_primera_opcion_no_viaja(self):
        listado = '<section class="trending"><div class="row"></div></section>'
        fetcher = Fetcher([Response(f"{BASE}/comics", listado)])
        source = source_class()(fetcher)

        await source.search("", 1, {"status": "Completed"})

        self.assertEqual(fetcher.requests[0][2]["params"], [])

    async def test_ficha_excluye_la_sinopsis_de_los_detalles(self):
        ficha = """
        <div class="page-content"><div class="text-details">
          <div class="name-rating">Gato</div>
          <img class="img-details" src="/gato.jpg">
          <p class="sec">Una historia.</p>
          <div class="soft-details">
            <p class="sec"><span>Autor</span>Kim</p>
            <p class="sec"><span>Artista</span>Lee</p>
            <p class="sec"><span>Status</span>En emisión</p>
            <p class="sec"><span>Generos</span>Drama, Romance</p>
          </div>
        </div></div>
        """
        fetcher = Fetcher([Response(f"{BASE}/comic/gato", ficha)])
        source = source_class()(fetcher)

        manga = await source.details("comic/gato")

        self.assertEqual((manga.title, manga.description), ("Gato", "Una historia."))
        self.assertEqual((manga.author, manga.artist, manga.status), ("Kim", "Lee", "ongoing"))
        self.assertEqual(manga.content_tags, ("Drama", "Romance"))

    async def test_capitulos_y_lector(self):
        ficha = """
        <div class="page-content">
          <div class="card-caps" onclick="window.location.href='/leer/gato-2'">
            <div class="text-cap"><span class="color-white">Capítulo 2</span>
              <span class="color-medium-gray">05/08/2026</span></div></div>
        </div>
        """
        lector = '<div class="page-content"><div class="img-blade"><img data-src="/p/1.jpg"></div></div>'
        fetcher = Fetcher([
            Response(f"{BASE}/comic/gato", ficha),
            Response(f"{BASE}/leer/gato-2", lector),
        ])
        source = source_class()(fetcher)

        chapters = await source.chapters("comic/gato")
        pages = await source.pages(chapters[0])

        self.assertEqual(
            [(c.source_id, c.title, c.number, c.uploaded_at) for c in chapters],
            [("leer/gato-2", "Capítulo 2", 2.0, "2026-08-05T00:00:00")],
        )
        self.assertEqual([page.source_id for page in pages], [f"{BASE}/p/1.jpg"])
        self.assertEqual(source.capabilities.requests_per_minute, 180)


if __name__ == "__main__":
    unittest.main()
