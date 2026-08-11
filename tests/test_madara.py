from __future__ import annotations

import sys
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parents[1]))

from engines.madara import (
    EsMi2MangaSource,
    MadaraSource,
    _cover_url,
    _cuerpo_de_formulario,
    _image_url,
    _parse_html,
)


class Response:
    def __init__(self, url, text): self.url, self.text, self.status_code = url, text, 200
    def raise_for_status(self): pass


class Fetcher:
    def __init__(self, responses): self.responses, self.requests = responses, []
    async def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs)); return self.responses.pop(0)


def source_class(**attrs):
    return type("Generated", (MadaraSource,), {"name": "test", "base_url": "https://aedexnox.akan01.com", **attrs})


class FormBodyTest(unittest.TestCase):
    """httpx 0.28 aborta si `data` es una lista de pares sobre un cliente async.

    Se queda en `RuntimeError: Attempted to send an sync request with an AsyncClient
    instance` y la fuente no llega a hacer NI UNA peticion: browse, latest y search fallan
    de golpe (haremdekira, "no carga nada" en la validacion manual).
    """

    def test_converts_a_list_of_pairs_into_a_dict(self):
        kwargs = _cuerpo_de_formulario({"data": [("action", "load"), ("page", "0")]})

        self.assertEqual(kwargs["data"], {"action": "load", "page": "0"})

    def test_leaves_a_dict_untouched(self):
        original = {"data": {"action": "load"}}

        self.assertEqual(_cuerpo_de_formulario(original)["data"], {"action": "load"})

    def test_does_not_touch_requests_without_body(self):
        kwargs = {"params": {"page": "2"}}

        self.assertEqual(_cuerpo_de_formulario(kwargs), {"params": {"page": "2"}})

    def test_does_not_mutate_the_caller_kwargs(self):
        original = {"data": [("action", "load")]}

        _cuerpo_de_formulario(original)

        self.assertEqual(original["data"], [("action", "load")])

    def test_the_real_madara_bodies_do_not_lose_keys(self):
        """Las claves van indexadas (`vars[meta_query][0][key]`), nunca se repiten."""
        cuerpos = [
            EsMi2MangaSource._esmi_load_more(1, True),
            EsMi2MangaSource._esmi_load_more(3, False),
            EsMi2MangaSource._esmi_search_load_more(
                2,
                "gato",
                {
                    "order": "views",
                    "adult": "1",
                    "genres": ["accion", "drama"],
                    "status": ["end", "on-going"],
                    "author": "alguien",
                    "genre_condition": "1",
                },
            ),
        ]

        for cuerpo in cuerpos:
            with self.subTest(campos=len(cuerpo)):
                self.assertEqual(len(dict(cuerpo)), len(cuerpo))


class CleartextTest(unittest.TestCase):
    """El sitio se sirve por https pero emite las imagenes con http:// en claro.

    Android bloquea el cleartext desde API 28, asi que esas paginas nunca llegaban al
    lector aunque el servidor respondiese 200 (catharsisworld: 8/8 paginas y 16/16
    portadas). Se promueven solo las del propio host.
    """

    BASE = "https://catharsisworld.dig-it.info"

    def test_promotes_same_host_pages_to_https(self):
        node = _parse_html(
            '<img src="http://catharsisworld.dig-it.info/wp-content/p.webp">'
        ).descendants("img")[0]

        self.assertEqual(
            _image_url(node, self.BASE),
            "https://catharsisworld.dig-it.info/wp-content/p.webp",
        )

    def test_promotes_the_background_image_too(self):
        node = _parse_html(
            '<a style="background-image:url(http://catharsisworld.dig-it.info/c.webp)"></a>'
        ).descendants("a")[0]

        self.assertEqual(
            _image_url(node, self.BASE),
            "https://catharsisworld.dig-it.info/c.webp",
        )

    def test_leaves_third_party_hosts_untouched(self):
        """Un CDN ajeno puede no tener certificado valido: no se toca."""
        node = _parse_html('<img src="http://cdn.ajeno.test/p.webp">').descendants("img")[0]

        self.assertEqual(_image_url(node, self.BASE), "http://cdn.ajeno.test/p.webp")

    def test_leaves_http_sites_untouched(self):
        """Si el propio sitio solo habla http, forzar https lo romperia."""
        node = _parse_html('<img src="http://viejo.test/p.webp">').descendants("img")[0]

        self.assertEqual(_image_url(node, "http://viejo.test"), "http://viejo.test/p.webp")

    def test_https_urls_are_unchanged(self):
        node = _parse_html(
            '<img src="https://catharsisworld.dig-it.info/p.webp">'
        ).descendants("img")[0]

        self.assertEqual(
            _image_url(node, self.BASE),
            "https://catharsisworld.dig-it.info/p.webp",
        )


class ImageUrlTest(unittest.TestCase):
    def test_img_attributes_still_win_over_the_style(self):
        """El fallback es aditivo: donde ya habia <img>, el resultado no cambia."""
        node = _parse_html(
            '<img src="/a.webp" style="background-image:url(/b.webp)">'
        ).descendants("img")[0]

        self.assertEqual(_image_url(node, "https://x.test"), "https://x.test/a.webp")

    def test_reads_the_background_image_when_there_is_no_img_attribute(self):
        node = _parse_html('<a style="background-image:url(/cover.webp)"></a>').descendants("a")[0]

        self.assertEqual(_image_url(node, "https://x.test"), "https://x.test/cover.webp")

    def test_accepts_the_url_with_and_without_quotes(self):
        # Las comillas dobles de dentro del url() solo sobreviven si el atributo
        # va en comillas simples o vienen escapadas: si no, el parser trunca el valor.
        for markup in (
            '<a style="background-image:url(/c.webp)"></a>',
            '<a style="background-image:url(\'/c.webp\')"></a>',
            "<a style='background-image:url(\"/c.webp\")'></a>",
            '<a style="background-image:url(&quot;/c.webp&quot;)"></a>',
            '<a style="background-image: url( /c.webp )"></a>',
            '<a style="background:url(/c.webp) center/cover no-repeat"></a>',
        ):
            with self.subTest(markup=markup):
                node = _parse_html(markup).descendants("a")[0]
                self.assertEqual(_image_url(node, "https://x.test"), "https://x.test/c.webp")

    def test_ignores_a_style_that_carries_no_background(self):
        node = _parse_html('<a style="color:red;display:flex"></a>').descendants("a")[0]

        self.assertEqual(_image_url(node, "https://x.test"), "")

    def test_cover_url_prefers_the_img_and_falls_back_to_the_container_style(self):
        with_image = _parse_html(
            '<div style="background-image:url(/skin.webp)"><img src="/real.webp"></div>'
        ).descendants("div")[0]
        without_image = _parse_html('<div style="background-image:url(/skin.webp)"></div>').descendants("div")[0]

        self.assertEqual(_cover_url(with_image, "https://x.test"), "https://x.test/real.webp")
        self.assertEqual(_cover_url(without_image, "https://x.test"), "https://x.test/skin.webp")

    def test_cover_url_is_none_when_there_is_nothing_to_find(self):
        node = _parse_html("<div><span>sin portada</span></div>").descendants("div")[0]

        self.assertIsNone(_cover_url(node, "https://x.test"))


class TempleScanCoversTest(unittest.IsolatedAsyncioTestCase):
    """Temple Scan es un Madara re-skineado con Tailwind: 0 <img>, 10 background-image."""

    MARKUP = """
    <div class="grid">
      <a href="https://aedexnox.akan01.com/serie/deja-de-fumar/" title="¡Deja De Fumar!"
         style="background-image:url(https://aedexnox.akan01.com/wp-content/uploads/2025/03/PT-Fumar.webp)"
         class="flex flex-col bg-cover bg-center relative"></a>
      <a href="https://aedexnox.akan01.com/serie/plan-de-intercambio-de-madres/" title="Plan de Intercambio"
         style="background-image:url(https://aedexnox.akan01.com/wp-content/uploads/2025/03/PT-Intercambio.jpg)"
         class="flex flex-col bg-cover bg-center relative"></a>
    </div>
    """

    async def test_recovers_the_cover_from_the_anchor_fallback(self):
        source = source_class(manga_substring="serie")(Fetcher([]))

        series = source._series(self.MARKUP, ("page-item-detail", "manga__item"))

        self.assertEqual(len(series), 2)
        self.assertEqual(
            [item.cover_url for item in series],
            [
                "https://aedexnox.akan01.com/wp-content/uploads/2025/03/PT-Fumar.webp",
                "https://aedexnox.akan01.com/wp-content/uploads/2025/03/PT-Intercambio.jpg",
            ],
        )

    async def test_recovers_the_cover_from_the_classic_madara_card(self):
        markup = """
        <div class="page-item-detail">
          <a href="/serie/demo/" style="background-image:url(/wp-content/demo.webp)">
            <div class="post-title"><h3><a href="/serie/demo/">Demo</a></h3></div>
          </a>
        </div>
        """
        source = source_class(manga_substring="serie")(Fetcher([]))

        series = source._series(markup, ("page-item-detail", "manga__item"))

        self.assertEqual(series[0].cover_url, "https://aedexnox.akan01.com/wp-content/demo.webp")

    async def test_a_classic_madara_with_img_keeps_working(self):
        markup = """
        <div class="page-item-detail">
          <div class="post-title"><h3><a href="/serie/clasico/">Clasico</a></h3></div>
          <img data-src="/wp-content/clasico.jpg" src="/placeholder.gif">
        </div>
        """
        source = source_class(manga_substring="serie")(Fetcher([]))

        series = source._series(markup, ("page-item-detail", "manga__item"))

        self.assertEqual(series[0].cover_url, "https://aedexnox.akan01.com/wp-content/clasico.jpg")


class ChapterDeduplicationTest(unittest.IsolatedAsyncioTestCase):
    """El fallback recorre li, div y tr: un ancla anidada entra una vez por contenedor."""

    async def test_the_nested_fallback_no_longer_repeats_a_chapter(self):
        markup = """
        <html><body>
          <div class="chapters">
            <div class="wrap">
              <li><a href="/serie/deja-de-fumar/capitulo-105/">Capitulo 105</a></li>
            </div>
            <li><a href="/serie/deja-de-fumar/capitulo-104/">Capitulo 104</a></li>
          </div>
        </body></html>
        """
        source = source_class(manga_substring="serie")(
            Fetcher([Response("https://aedexnox.akan01.com/serie/deja-de-fumar/", markup)])
        )

        chapters = await source.chapters("https://aedexnox.akan01.com/serie/deja-de-fumar/")

        ids = [item.source_id for item in chapters]
        self.assertEqual(len(ids), len(set(ids)), f"capitulos repetidos: {ids}")
        self.assertEqual(
            ids,
            [
                "https://aedexnox.akan01.com/serie/deja-de-fumar/capitulo-105/?style=list",
                "https://aedexnox.akan01.com/serie/deja-de-fumar/capitulo-104/?style=list",
            ],
        )
        self.assertEqual([item.number for item in chapters], [105.0, 104.0])

    async def test_the_classic_madara_list_is_untouched(self):
        markup = """
        <html><body><ul>
          <li class="wp-manga-chapter"><a href="/manga/demo/chapter-2/">Chapter 2</a></li>
          <li class="wp-manga-chapter"><a href="/manga/demo/chapter-1/">Chapter 1</a></li>
        </ul></body></html>
        """
        source = source_class()(Fetcher([Response("https://aedexnox.akan01.com/manga/demo/", markup)]))

        chapters = await source.chapters("https://aedexnox.akan01.com/manga/demo/")

        self.assertEqual([item.number for item in chapters], [2.0, 1.0])


class RespuestaConEstado:
    """Doble que si distingue el codigo: el 404 de `page/N/` es informacion."""

    def __init__(self, url, text, status_code=200):
        self.url, self.text, self.status_code = url, text, status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"raise_for_status no deberia dispararse: {self.status_code}")


class FetcherQueLanza:
    """Fetcher que lanza el 404 desde `request`, como hace la app de verdad.

    `RateLimitedClient.request` llama a `raise_for_status()` ANTES de devolver, asi
    que el bundle nunca ve una respuesta 404: ve una excepcion. El doble `Fetcher`
    de este archivo no lo hace, y por eso una guarda escrita como
    `if response.status_code == 404` pasaba los tests y seguia reventando en
    produccion. Este doble existe para que esa diferencia no se vuelva a colar.
    """

    def __init__(self, status_code=404):
        self.status_code = status_code
        self.requests = []

    async def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        raise httpx.HTTPStatusError(
            f"Client error '{self.status_code}' for url '{url}'",
            request=httpx.Request(method, url),
            response=httpx.Response(self.status_code, request=httpx.Request(method, url)),
        )


class PaginacionAgotadaTest(unittest.IsolatedAsyncioTestCase):
    """Pedir `page/N/` mas alla de la ultima pagina devuelve 404 en WordPress.

    Ese 404 es el marcador de fin de catalogo, no un fallo. Al propagarlo, la app
    reventaba en cuanto el usuario hacia scroll: infrafandub tiene 18 series en una
    sola pagina y crasheaba a los ~2 segundos con "La fuente no encontro el recurso"
    (validacion humana de infrafandub_es e inventariooculto_es).
    """

    TARJETA = """
    <div class="page-item-detail">
      <div class="post-title"><h3><a href="/manga/gato/">Gato</a></h3></div>
      <img data-src="/gato.jpg">
    </div>
    """

    async def test_el_404_lanzado_por_el_fetcher_agota_el_catalogo(self):
        """El caso REAL: el fetcher de la app lanza en vez de devolver la respuesta."""
        source = source_class()(FetcherQueLanza())

        self.assertEqual(await source.browse("popular", 2), [])

    async def test_el_404_lanzado_en_la_primera_pagina_sigue_viajando(self):
        source = source_class()(FetcherQueLanza())

        with self.assertRaises(httpx.HTTPStatusError):
            await source.browse("popular", 1)

    async def test_un_500_lanzado_sigue_viajando_aunque_sea_pagina_posterior(self):
        """Solo el 404 significa "no hay mas"; el resto son fallos de verdad."""
        source = source_class()(FetcherQueLanza(status_code=500))

        with self.assertRaises(httpx.HTTPStatusError):
            await source.browse("popular", 2)

    async def test_el_404_devuelto_como_respuesta_tambien_agota(self):
        fetcher = Fetcher([
            RespuestaConEstado("https://aedexnox.akan01.com/manga/page/2/", "", 404),
        ])
        source = source_class()(fetcher)

        self.assertEqual(await source.browse("popular", 2), [])

    async def test_el_404_de_la_primera_pagina_sigue_siendo_un_fallo(self):
        fetcher = Fetcher([
            RespuestaConEstado("https://aedexnox.akan01.com/manga/", "", 404),
        ])
        source = source_class()(fetcher)

        with self.assertRaises(AssertionError):
            await source.browse("popular", 1)

    async def test_una_pagina_posterior_con_series_se_devuelve_igual(self):
        fetcher = Fetcher([
            RespuestaConEstado("https://aedexnox.akan01.com/manga/page/2/", self.TARJETA),
        ])
        source = source_class()(fetcher)

        series = await source.browse("popular", 2)

        self.assertEqual([item.title for item in series], ["Gato"])


if __name__ == "__main__":
    unittest.main()
