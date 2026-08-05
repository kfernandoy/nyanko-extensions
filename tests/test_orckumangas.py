from __future__ import annotations

import unittest
from pathlib import Path

from tools.generate import _manual_bundle

BASE = "https://orckumangas.com"


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
    path = Path(__file__).parents[1] / "engines" / "manual" / "orckumangas_es.py"
    namespace = {"__name__": "test_orckumangas_bundle"}
    exec(compile(_manual_bundle(path), str(path), "exec"), namespace)
    return namespace["SOURCE"]


TARJETAS = """
<div class="card"><a href="/manga/gato"><img src="/gato.jpg">
  <h3>Gato<span>extra</span></h3></a></div>
<div class="card"><a href="/manga/lobo"><img src="/lobo.jpg"><h3>Lobo</h3></a></div>
<div class="flex"><a href="/ranking.php?page=3">Siguiente</a></div>
"""


class OrckuMangasTest(unittest.IsolatedAsyncioTestCase):
    async def test_ranking_usa_tarjetas_y_detecta_siguiente(self):
        fetcher = Fetcher([Response(f"{BASE}/ranking.php", TARJETAS)])
        source = source_class()(fetcher)

        result = await source.browse("popular", 2)

        self.assertEqual(fetcher.requests[0][1], f"{BASE}/ranking.php")
        self.assertEqual(fetcher.requests[0][2]["params"], {"page": "2"})
        # h3 lleva un span anidado: solo cuenta el texto propio.
        self.assertEqual(
            [(item.source_id, item.title) for item in result["items"]],
            [("manga/gato", "Gato"), ("manga/lobo", "Lobo")],
        )
        self.assertEqual(result["items"][0].cover_url, f"{BASE}/gato.jpg")
        self.assertTrue(result["has_more"])

    async def test_novedades_no_paginan(self):
        recientes = '<div><a class="block" href="/manga/oso"><img src="/oso.jpg"><h3>Oso</h3></a></div>'
        fetcher = Fetcher([Response(f"{BASE}/index.php", recientes)])
        source = source_class()(fetcher)

        result = await source.browse("latest", 4)

        self.assertEqual(fetcher.requests[0][2]["params"], {"filter_chapters": "1", "type": ""})
        self.assertEqual([item.source_id for item in result["items"]], ["manga/oso"])
        self.assertFalse(result["has_more"])

    async def test_busqueda_por_texto_ignora_filtros(self):
        fetcher = Fetcher([Response(f"{BASE}/buscador.php", TARJETAS)])
        source = source_class()(fetcher)

        await source.search("gato", 2, {"genre": "1", "type": "manga"})

        self.assertEqual(fetcher.requests[0][1], f"{BASE}/buscador.php")
        self.assertEqual(fetcher.requests[0][2]["params"], {"q": "gato", "page": "2"})

    async def test_biblioteca_arma_los_filtros(self):
        fetcher = Fetcher([Response(f"{BASE}/biblioteca.php", TARJETAS)])
        source = source_class()(fetcher)

        await source.search("", 3, {"genre": "5", "status": "completed"})

        self.assertEqual(fetcher.requests[0][1], f"{BASE}/biblioteca.php")
        self.assertEqual(fetcher.requests[0][2]["params"], [
            ("page", "3"), ("genre", "5"), ("type", ""), ("status", "completed"),
        ])

    async def test_ficha_con_etiquetas_por_span(self):
        ficha = """
        <main><div class="card"><h1>Gato</h1><img src="/gato.jpg">
          <div><span>Autor</span>Kim</div>
          <div><span>Artista</span>Lee</div>
          <div><span>Estado</span>completed</div>
          <a href="/biblioteca.php?genre=1">Acción</a>
          <a href="/biblioteca.php?genre=5">Romance</a>
          <p>Una historia.</p>
        </div></main>
        """
        fetcher = Fetcher([Response(f"{BASE}/manga/gato", ficha)])
        source = source_class()(fetcher)

        manga = await source.details("manga/gato")

        self.assertEqual((manga.title, manga.author, manga.artist), ("Gato", "Kim", "Lee"))
        self.assertEqual((manga.status, manga.description), ("completed", "Una historia."))
        self.assertEqual(manga.content_tags, ("Acción", "Romance"))

    async def test_capitulos_recorren_todas_las_paginas(self):
        primera = """
        <div class="card"><div class="grid">
          <a class="block" href="/leer/gato-2"><span>Capítulo 2</span></a>
        </div></div>
        <div><a href="/manga/gato?order=desc&page=2">2</a></div>
        """
        segunda = """
        <div class="card"><div class="grid">
          <a class="block" href="/leer/gato-1"><span>Capítulo 1</span></a>
        </div></div>
        """
        fetcher = Fetcher([
            Response(f"{BASE}/manga/gato", primera),
            Response(f"{BASE}/manga/gato", segunda),
        ])
        source = source_class()(fetcher)

        chapters = await source.chapters("manga/gato")

        self.assertEqual(fetcher.requests[0][2]["params"], {"order": "desc", "page": "1"})
        self.assertEqual(fetcher.requests[1][2]["params"], {"order": "desc", "page": "2"})
        self.assertEqual(
            [(c.source_id, c.title, c.number) for c in chapters],
            [("leer/gato-2", "Capítulo 2", 2.0), ("leer/gato-1", "Capítulo 1", 1.0)],
        )

    async def test_lector_y_metadatos(self):
        lector = '<div class="chapter-images"><img src="/p/1.jpg"><img src="/p/2.jpg"></div>'
        fetcher = Fetcher([Response(f"{BASE}/leer/gato-2", lector)])
        source = source_class()(fetcher)

        pages = await source.pages("leer/gato-2")

        self.assertEqual([page.source_id for page in pages], [f"{BASE}/p/1.jpg", f"{BASE}/p/2.jpg"])
        self.assertEqual(source.capabilities.content_warning, "nsfw")
        self.assertEqual(source.capabilities.requests_per_minute, 180)


if __name__ == "__main__":
    unittest.main()
