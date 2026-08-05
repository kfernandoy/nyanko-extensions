from __future__ import annotations

import unittest
from pathlib import Path

from tools.generate import _manual_bundle

BASE = "https://myreadingmanga.info"


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


def source_class(extension_id: str = "myreadingmanga_es"):
    path = Path(__file__).parents[1] / "engines" / "manual" / f"{extension_id}.py"
    namespace = {"__name__": f"test_{extension_id}_bundle"}
    exec(compile(_manual_bundle(path), str(path), "exec"), namespace)
    return namespace["SOURCE"]


def listado(count: int, total: str = "40") -> str:
    articles = "".join(
        f"""<article><a rel="bookmark" href="/serie-{index}/">[Autor] Título {index} (Spanish)</a>
        <a class="entry-image-link"><img data-src="{BASE}/wp/foto-{index}-300x400.jpg"></a></article>"""
        for index in range(count)
    )
    return f'<span class="ep-search-count">Se encontraron {total} resultados</span>{articles}'


class MyReadingMangaTest(unittest.IsolatedAsyncioTestCase):
    async def test_populares_son_el_listado_aleatorio(self):
        fetcher = Fetcher([Response(f"{BASE}/page/2/", listado(1))])
        source = source_class()(fetcher)

        result = await source.browse("popular", 2)

        self.assertEqual(fetcher.requests[0][1], f"{BASE}/page/2/")
        self.assertEqual(
            fetcher.requests[0][2]["params"],
            {"s": "", "ep_sort": "rand", "ep_filter_lang": "Spanish"},
        )
        # El titulo pierde el autor entre corchetes y el idioma entre parentesis.
        self.assertEqual(result["items"][0].title, "Título 0")
        # La portada pierde el sufijo de redimension.
        self.assertEqual(result["items"][0].cover_url, f"{BASE}/wp/foto-0.jpg")

    async def test_recientes_usan_la_ruta_de_idioma(self):
        pagina = '<article><a rel="bookmark" href="/x/">X</a></article><li class="pagination-next"></li>'
        fetcher = Fetcher([
            Response(f"{BASE}/lang/spanish", pagina),
            Response(f"{BASE}/lang/jp/page/2/", pagina),
        ])

        castellano = await source_class()(fetcher).browse("latest", 1)
        await source_class("myreadingmanga_ja")(fetcher).browse("latest", 2)

        self.assertEqual(fetcher.requests[0][1], f"{BASE}/lang/spanish")
        # El japones usa "jp", no "japanese".
        self.assertEqual(fetcher.requests[1][1], f"{BASE}/lang/jp/page/2/")
        self.assertTrue(castellano["has_more"])

    async def test_busqueda_acumula_lo_leido_contra_el_total(self):
        fetcher = Fetcher([
            Response(f"{BASE}/page/1/", listado(30)),
            Response(f"{BASE}/page/2/", listado(10)),
        ])
        source = source_class()(fetcher)

        primera = await source.search("gato", 1, {"sort": "date_asc", "genre": "yaoi"})
        segunda = await source.search("gato", 2, {})

        self.assertEqual(fetcher.requests[0][2]["params"], [
            ("s", "gato"), ("ep_filter_lang", "Spanish"),
            ("ep_sort", "date_asc"), ("ep_filter_genre", "yaoi"),
        ])
        # 30 de 40 -> hay mas; 40 de 40 -> no.
        self.assertTrue(primera["has_more"])
        self.assertFalse(segunda["has_more"])

    async def test_desactivar_el_idioma_lo_quita_de_la_consulta(self):
        fetcher = Fetcher([Response(f"{BASE}/page/1/", listado(0))])
        source = source_class()(fetcher)

        await source.search("gato", 1, {"enforce_lang": False})

        self.assertEqual(fetcher.requests[0][2]["params"], [("s", "gato"), ("ep_sort", "date")])

    async def test_filtros_dinamicos_salen_de_las_paginas_cacheadas(self):
        generos = '<div class="tagcloud"><a href="/genre/yaoi/">Yaoi</a></div>'
        indice = '<div class="tag-groups-alphabetical-index"><a href="/tags/oneshot/">Oneshot</a></div>'
        fetcher = Fetcher([
            Response(f"{BASE}/", generos),
            Response(f"{BASE}/tags/", indice),
            Response(f"{BASE}/cats/", indice),
            Response(f"{BASE}/pairing/", indice),
            Response(f"{BASE}/group/", indice),
        ])
        source = source_class()(fetcher)

        filters = await source.get_filters()
        cached = await source.get_filters()

        self.assertEqual([value.id for value in filters], [
            "enforce_lang", "sort", "genre", "tag", "category", "pairing", "group",
        ])
        self.assertEqual(filters[2].options, [("", "Any"), ("yaoi", "Yaoi")])
        self.assertEqual(filters[3].options, [("", "Any"), ("oneshot", "Oneshot")])
        # La segunda llamada no vuelve a pedir las cinco paginas.
        self.assertEqual(len(fetcher.requests), 5)
        self.assertEqual(cached[2].options, filters[2].options)

    async def test_capitulos_rellenan_hasta_el_ultimo_numero(self):
        ficha = """
        <h1>[Autor] Título (Spanish)</h1>
        <span class="entry-time">Aug 05, 2026</span>
        <a class="page-numbers" href="/x/2">2</a>
        <a class="page-numbers" href="/x/3">3</a>
        <a class="page-numbers next" href="/x/4">Next</a>
        """
        fetcher = Fetcher([Response(f"{BASE}/serie-0/", ficha)])
        source = source_class()(fetcher)

        chapters = await source.chapters("serie-0/")

        # "page-numbers next" no cuenta: el Kotlin exige la clase exacta.
        self.assertEqual(
            [(c.source_id, c.title) for c in chapters],
            [("serie-0/3", "Part 3"), ("serie-0/2", "Part 2"), ("serie-0/1", "Part 1")],
        )
        self.assertEqual(chapters[0].uploaded_at, "2026-08-05T00:00:00")

    async def test_ficha_y_lector(self):
        ficha = """
        <h1>[Kim] Título (Spanish)</h1>
        <div class="entry-terms"><a href="/group/equipo/">Equipo</a></div>
        <div class="entry-content"><p>Una historia.</p><p>algo | con barra</p></div>
        <a href="/status/ongoing/">Ongoing</a>
        <span class="entry-categories"><a>Romance</a></span>
        """
        lector = """
        <div class="entry-content"><img data-src="/p/1.jpg"><img src="/p/2.png"></div>
        <div class="separator"><img data-src="/p/3.webp"></div>
        """
        fetcher = Fetcher([
            Response(f"{BASE}/serie-0/", ficha),
            Response(f"{BASE}/serie-0/1", lector),
        ])
        source = source_class()(fetcher)

        manga = await source.details("serie-0/")
        pages = await source.pages("serie-0/1")

        self.assertEqual((manga.title, manga.author, manga.status), ("Título", "Kim", "ongoing"))
        self.assertIn("Scanlated by: Equipo", manga.description)
        self.assertEqual(
            [page.source_id for page in pages],
            [f"{BASE}/p/1.jpg", f"{BASE}/p/2.png", f"{BASE}/p/3.webp"],
        )


if __name__ == "__main__":
    unittest.main()
