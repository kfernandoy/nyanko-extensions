from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from engines.madara import MadaraSource, _cover_url, _image_url, _parse_html


class Response:
    def __init__(self, url, text): self.url, self.text, self.status_code = url, text, 200
    def raise_for_status(self): pass


class Fetcher:
    def __init__(self, responses): self.responses, self.requests = responses, []
    async def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs)); return self.responses.pop(0)


def source_class(**attrs):
    return type("Generated", (MadaraSource,), {"name": "test", "base_url": "https://aedexnox.akan01.com", **attrs})


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


if __name__ == "__main__":
    unittest.main()
