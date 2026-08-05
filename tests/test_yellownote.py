from __future__ import annotations

import unittest
from pathlib import Path

from tools.generate import _manual_bundle

BASE = "https://es.xchina.co"


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


def source_class(extension_id: str = "yellownote_es"):
    path = Path(__file__).parents[1] / "engines" / "manual" / f"{extension_id}.py"
    namespace = {"__name__": f"test_{extension_id}_bundle"}
    exec(compile(_manual_bundle(path), str(path), "exec"), namespace)
    return namespace["SOURCE"]


LISTADO = """
<div class="list photo-list">
  <div class="item photo"><a href="/photos/id-123.html" title="Álbum">
    <div class="img" style="background-image: url('https://cdn/t.jpg')"></div></a>
    <div class="tags"><div>Nuevo</div><div>30P + 2V</div></div></div>
</div>
<div class="pager"><a class="pager-next" href="/photos/2.html">&gt;</a></div>
"""


class YellowNoteTest(unittest.IsolatedAsyncioTestCase):
    async def test_populares_y_recientes_usan_rutas_distintas(self):
        fetcher = Fetcher([
            Response(f"{BASE}/photos/sort-hot/2.html", LISTADO),
            Response(f"{BASE}/photos/3.html", LISTADO),
        ])
        source = source_class()(fetcher)

        popular = await source.browse("popular", 2)
        await source.browse("latest", 3)

        self.assertEqual(fetcher.requests[0][1], f"{BASE}/photos/sort-hot/2.html")
        self.assertEqual(fetcher.requests[1][1], f"{BASE}/photos/3.html")
        # El recuento de medios se pega al titulo y la portada sale del style.
        self.assertEqual(
            [(item.source_id, item.title, item.cover_url) for item in popular["items"]],
            [("photos/id-123.html", "Álbum(30P + 2V)", "https://cdn/t.jpg")],
        )
        self.assertTrue(popular["has_more"])

    async def test_busqueda_por_texto_reemplaza_la_categoria(self):
        fetcher = Fetcher([
            Response(f"{BASE}/photos/keyword-gato/2.html", LISTADO),
            Response(f"{BASE}/photos/album-5/sort-hot/1.html", LISTADO),
        ])
        source = source_class()(fetcher)

        await source.search("gato", 2, {"category": "photos/album-5", "sort": ""})
        await source.search("", 1, {"category": "photos/album-5", "sort": "sort-hot"})

        self.assertEqual(fetcher.requests[0][1], f"{BASE}/photos/keyword-gato/2.html")
        self.assertEqual(fetcher.requests[1][1], f"{BASE}/photos/album-5/sort-hot/1.html")

    async def test_los_filtros_hablan_el_idioma_del_bundle(self):
        castellano = source_class()(None).get_filters()
        ingles = source_class("yellownote_en")(None).get_filters()

        self.assertEqual(castellano[0].name, "Ordenar por")
        self.assertEqual(ingles[0].name, "Sort by")
        self.assertEqual(ingles[0].options[1], ("sort-hot", "Popularity"))
        self.assertEqual(len(castellano[1].options), 65)
        self.assertEqual(castellano[1].options[0][0], "photos/album-1")

    async def test_ficha_compone_el_titulo_desde_los_iconos(self):
        ficha = """
        <div class="info-card photo-detail">
          <div class="item"><div class="icon"><i class="fa-address-card"></i></div>
            <div class="text">Modelo</div></div>
          <div class="item"><div class="icon"><i class="fa-image"></i></div>
            <div class="text">30P</div></div>
          <div class="item"><div class="icon"><i class="fa-file"></i></div>
            <div class="text">No.42</div></div>
          <div class="item"><div class="icon"><i class="fa-video-camera"></i></div>
            <div class="text"><span>Estudio</span><span>-</span></div></div>
          <div class="item"><div class="icon"><i class="fa-tags"></i></div>
            <div class="text"><span>Cosplay</span></div></div>
          <div class="item floating">Autor</div>
        </div>
        """
        fetcher = Fetcher([Response(f"{BASE}/photos/id-123.html", ficha)])
        source = source_class()(fetcher)

        manga = await source.details("photos/id-123.html")

        self.assertEqual(manga.title, "Modelo No.42(30P)")
        self.assertEqual((manga.author, manga.status), ("Autor", "completed"))
        # El guion de la categoria no cuenta como etiqueta.
        self.assertEqual(manga.content_tags, ("Estudio", "Cosplay"))

    async def test_cada_pagina_del_album_es_un_capitulo(self):
        ficha = """
        <div class="info-card photo-detail">
          <div class="item"><div class="icon"><i class="fa-calendar-days"></i></div>
            <div class="text">2026.08.05</div></div>
        </div>
        <div class="pager"><a class="pager-num">1</a><a class="pager-num">3</a></div>
        """
        fetcher = Fetcher([Response(f"{BASE}/photos/id-123.html", ficha)])
        source = source_class()(fetcher)

        chapters = await source.chapters("photos/id-123.html")

        self.assertEqual(
            [(c.source_id, c.title) for c in chapters],
            [
                ("photos/id-123/3.html", "Page 3"),
                ("photos/id-123/2.html", "Page 2"),
                ("photos/id-123/1.html", "Page 1"),
            ],
        )
        self.assertEqual(chapters[0].uploaded_at, "2026-08-05T00:00:00")

    async def test_lector_pide_el_jpg_en_calidad_original(self):
        lector = """
        <div class="list photo-items">
          <div class="item photo-image">
            <div class="img" style="background-image: url('https://cdn/1_600x0.webp')"></div></div>
          <div class="item photo-image">
            <div class="img" style="background-image: url('https://cdn/2.jpg')"></div></div>
        </div>
        """
        fetcher = Fetcher([Response(f"{BASE}/photos/id-123/1.html", lector)])
        source = source_class()(fetcher)

        pages = await source.pages("photos/id-123/1.html")

        self.assertEqual([page.source_id for page in pages], ["https://cdn/1.jpg", "https://cdn/2.jpg"])
        self.assertEqual(source.get_preferences()[0].default, "original")
        self.assertEqual(source.capabilities.content_warning, "nsfw")


if __name__ == "__main__":
    unittest.main()
