from __future__ import annotations

import unittest
from pathlib import Path

from tools.generate import _manual_bundle

BASE = "https://raventard.xyz"


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
    path = Path(__file__).parents[1] / "engines" / "manual" / "ravenmanga_es.py"
    namespace = {"__name__": "test_ravenmanga_bundle"}
    exec(compile(_manual_bundle(path), str(path), "exec"), namespace)
    return namespace["SOURCE"]


PORTADA = """
<div id="div-diario"><figure><a href="/sr2/gato"><img src="/gato.jpg"></a>
  <figcaption>Gato</figcaption></figure></div>
<div id="div-semanal"><figure><a href="/sr2/gato"><img src="/gato.jpg"></a>
  <figcaption>Gato</figcaption></figure></div>
<div id="div-mensual"><figure><a href="/sr2/lobo"><img src="/lobo.jpg"></a>
  <figcaption>Lobo</figcaption></figure></div>
<section class="flex"><div class="grid">
  <figure><a href="/sr2/oso"><img src="/oso.jpg"></a><figcaption>Oso</figcaption></figure>
</div></section>
"""


# Sin query, search() lee /comics?page=N y parsea la grilla, no el JSON embebido.
CATALOGO = """
<section class="flex"><div class="grid">
  <figure><a href="/sr2/gato"><img src="/gato.jpg"></a><figcaption>Gato</figcaption></figure>
  <figure><a href="/sr2/zorro"><img src="/zorro.jpg"></a><figcaption>Zorro</figcaption></figure>
</div></section>
"""


class RavenMangaTest(unittest.IsolatedAsyncioTestCase):
    async def test_populares_deduplican_y_recientes_usan_la_grilla(self):
        # popular = rankings de la home + catalogo paginado; latest sigue leyendo la home.
        fetcher = Fetcher(
            [
                Response(BASE, PORTADA),
                Response(f"{BASE}/comics", CATALOGO),
                Response(BASE, PORTADA),
            ]
        )
        source = source_class()(fetcher)

        popular = await source.browse("popular")
        latest = await source.browse("latest")

        self.assertEqual(fetcher.requests[0][1], BASE)
        # Gato aparece en diario y semanal: distinctBy deja uno solo. Y aunque el catalogo
        # lo repita, no se duplica al concatenarlo detras de los destacados.
        self.assertEqual(
            [(item.source_id, item.title) for item in popular["items"]],
            [("sr2/gato", "Gato"), ("sr2/lobo", "Lobo"), ("sr2/zorro", "Zorro")],
        )
        self.assertEqual(popular["items"][0].cover_url, f"{BASE}/gato.jpg")
        self.assertEqual([item.source_id for item in latest["items"]], ["sr2/oso"])

    async def test_busqueda_local_filtra_el_json_embebido(self):
        listado = """
        <script>var proyectos = [
          {"nombre":"Gato Negro","slug":"gato-negro","portada":"https://cdn/gn.jpg"},
          {"nombre":"Lobo","slug":"lobo","portada":"https://cdn/l.jpg"}
        ];</script>
        """
        fetcher = Fetcher([Response(f"{BASE}/comics", listado)])
        source = source_class()(fetcher)

        result = await source.search("gato")

        self.assertEqual(fetcher.requests[0][1], f"{BASE}/comics")
        self.assertEqual(
            [(item.source_id, item.title, item.cover_url) for item in result["items"]],
            [("sr2/gato-negro", "Gato Negro", "https://cdn/gn.jpg")],
        )
        self.assertFalse(result["has_more"])

    async def test_busqueda_de_un_caracter_se_rechaza(self):
        source = source_class()(Fetcher([]))

        with self.assertRaises(ValueError):
            await source.search("a")

    async def test_listado_paginado_cuando_no_hay_consulta(self):
        pagina = """
        <section class="flex"><div class="grid">
          <figure><a href="/sr2/oso"><img src="/oso.jpg"></a><figcaption>Oso</figcaption></figure>
        </div></section>
        <nav><ul class="pagination"><li><a rel="next" href="/comics?page=3">3</a></li></ul></nav>
        """
        fetcher = Fetcher([Response(f"{BASE}/comics", pagina)])
        source = source_class()(fetcher)

        result = await source.search("", 2)

        self.assertEqual(fetcher.requests[0][2]["params"], {"page": "2"})
        self.assertEqual([item.source_id for item in result["items"]], ["sr2/oso"])
        self.assertTrue(result["has_more"])

    async def test_ficha_capitulos_y_lector_tras_el_formulario(self):
        ficha = """
        <section id="section-sinopsis"><p>Una historia.</p>
          <div class="flex"><div>Géneros</div><div><a><span>Acción</span></a>
          <a><span>Drama</span></a></div></div></section>
        <section id="section-list-cap"><div class="grid">
          <a href="/ver/gato-2"><div id="name">Capítulo 2</div><time>hace 3 días</time></a>
        </div></section>
        """
        puente = """
        <form id="redirectForm" method="post" action="/go">
          <input name="token" value="abc"><input name="id" value="7">
        </form>
        """
        lector = """
        <main class="contenedor-imagen"><section>
          <img src="/p/1.jpg"><img src="/p/2.jpg">
        </section></main>
        """
        fetcher = Fetcher([
            Response(f"{BASE}/sr2/gato", ficha),
            Response(f"{BASE}/sr2/gato", ficha),
            Response(f"{BASE}/ver/gato-2", puente),
            Response(f"{BASE}/go", lector),
        ])
        source = source_class()(fetcher)

        manga = await source.details("sr2/gato")
        chapters = await source.chapters("sr2/gato")
        pages = await source.pages(chapters[0])

        self.assertEqual((manga.description, manga.content_tags), ("Una historia.", ("Acción", "Drama")))
        self.assertEqual((chapters[0].source_id, chapters[0].title, chapters[0].number), ("ver/gato-2", "Capítulo 2", 2.0))
        self.assertIsNotNone(chapters[0].uploaded_at)
        # El lector exige reenviar los campos ocultos por POST.
        self.assertEqual(fetcher.requests[3][0], "POST")
        self.assertEqual(fetcher.requests[3][1], f"{BASE}/go")
        self.assertEqual(fetcher.requests[3][2]["data"], {"token": "abc", "id": "7"})
        self.assertEqual(fetcher.requests[3][2]["headers"]["Referer"], f"{BASE}/ver/gato-2")
        self.assertEqual([page.source_id for page in pages], [f"{BASE}/p/1.jpg", f"{BASE}/p/2.jpg"])
        self.assertEqual([page.index for page in pages], [0, 1])

    async def test_metadatos(self):
        source = source_class()(None)

        self.assertEqual(source.capabilities.content_warning, "safe")
        self.assertEqual(source.capabilities.requests_per_minute, 120)
        self.assertEqual(source.image_headers["Referer"], f"{BASE}/")
        self.assertEqual(source.get_filters(), [])


if __name__ == "__main__":
    unittest.main()
