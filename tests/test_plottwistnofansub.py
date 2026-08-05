from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.generate import _manual_bundle

BASE = "https://plotnofansub.com"


class Response:
    def __init__(self, url: str, payload) -> None:
        self.url = url
        self.text = payload if isinstance(payload, str) else json.dumps(payload)
        self.status_code = 200

    def raise_for_status(self) -> None:
        pass

    def json(self):
        return json.loads(self.text)


class Fetcher:
    def __init__(self, responses: list[Response]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, str, dict]] = []

    async def request(self, method: str, url: str, **kwargs):
        self.requests.append((method, url, kwargs))
        return self.responses.pop(0)


def source_class():
    path = Path(__file__).parents[1] / "engines" / "manual" / "plottwistnofansub_es.py"
    namespace = {"__name__": "test_plottwistnofansub_bundle"}
    exec(compile(_manual_bundle(path), str(path), "exec"), namespace)
    return namespace["SOURCE"]


GRID = """
<div class="manga-grid-v2"><figure>
  <a href="/manga/el-gato/" title="El Gato"></a>
  <img data-src="/covers/g.jpg" src="/ph.jpg"><figcaption>Otro</figcaption>
</figure></div>
<a class="next page-numbers" href="/biblioteca3/page/2/">2</a>
"""


def chapter_item(number: str, slug: str, extend: str = "", date: str = "agosto 5, 2026") -> str:
    return f"""<a class="mn-detail-chapter-item" href="/leer/{slug}/">
      <span class="mn-detail-chapter-name">{number}</span>
      <span class="mn-detail-chapter-extend">{extend}</span>
      <span class="mn-detail-chapter-date">{date}</span></a>"""


class PlotTwistNoFansubTest(unittest.IsolatedAsyncioTestCase):
    async def test_biblioteca_ordena_distinto_por_seccion(self):
        fetcher = Fetcher([
            Response(f"{BASE}/biblioteca3", GRID),
            Response(f"{BASE}/biblioteca3/page/2/", GRID),
            Response(f"{BASE}/biblioteca3", GRID),
        ])
        source = source_class()(fetcher)

        popular = await source.browse("popular", 1)
        await source.browse("latest", 2)
        await source.search("", 1)

        self.assertEqual(fetcher.requests[0][2]["params"], {"m_orderby": "trending"})
        self.assertEqual(fetcher.requests[1][1], f"{BASE}/biblioteca3/page/2/")
        self.assertEqual(fetcher.requests[1][2]["params"], {"m_orderby": "latest3"})
        self.assertEqual(fetcher.requests[2][2]["params"], {"m_orderby": "views3"})
        # El atributo title gana sobre el figcaption.
        self.assertEqual(
            [(item.source_id, item.title, item.cover_url) for item in popular["items"]],
            [("manga/el-gato/", "El Gato", f"{BASE}/covers/g.jpg")],
        )
        self.assertTrue(popular["has_more"])

    async def test_busqueda_por_texto_usa_el_buscador_de_wordpress(self):
        fetcher = Fetcher([Response(f"{BASE}/page/2/", GRID)])
        source = source_class()(fetcher)

        await source.search("gato", 2)

        self.assertEqual(fetcher.requests[0][1], f"{BASE}/page/2/")
        self.assertEqual(
            fetcher.requests[0][2]["params"], {"s": "gato", "post_type": "wp-manga"},
        )

    async def test_ficha_lee_los_pills_del_tema(self):
        ficha = """
        <h1 class="mn-detail-title">El Gato</h1>
        <div class="mn-detail-cover-frame"><img data-src="/covers/g.jpg"></div>
        <div class="mn-detail-synopsis">Una historia.</div>
        <div class="mn-detail-genres-desktop"><a>Acción</a><a>Romance</a></div>
        <div><span class="mn-detail-pill-value mn-st-emit">En emisión</span></div>
        <div><span class="mn-detail-pill-label">Autor</span>
          <span class="mn-detail-pill-value">Kim</span></div>
        """
        fetcher = Fetcher([Response(f"{BASE}/manga/el-gato/", ficha)])
        source = source_class()(fetcher)

        manga = await source.details("manga/el-gato/")

        self.assertEqual((manga.title, manga.description), ("El Gato", "Una historia."))
        self.assertEqual((manga.status, manga.author), ("ongoing", "Kim"))
        self.assertEqual(manga.content_tags, ("Acción", "Romance"))

    async def test_capitulos_combinan_html_y_ajax_sin_duplicar(self):
        ficha = f"""
        <div id="mn-detail-load-more" data-manga="42"></div>
        {chapter_item("3", "gato-3", "El giro")}
        """
        primera = {"success": True, "data": {
            # Repite el capitulo ya renderizado y anade uno nuevo.
            "html": chapter_item("3", "gato-3") + chapter_item("2", "gato-2"),
            "has_more": True,
        }}
        segunda = {"success": True, "data": {"html": chapter_item("1", "gato-1"), "has_more": False}}
        fetcher = Fetcher([
            Response(f"{BASE}/manga/el-gato/", ficha),
            Response(f"{BASE}/wp-admin/admin-ajax.php", primera),
            Response(f"{BASE}/wp-admin/admin-ajax.php", segunda),
        ])
        source = source_class()(fetcher)

        chapters = await source.chapters("manga/el-gato/")

        self.assertEqual(fetcher.requests[1][2]["data"], {
            "action": "plot_load_chapters", "manga_id": "42", "page": "1",
        })
        self.assertEqual(fetcher.requests[2][2]["data"]["page"], "2")
        self.assertEqual(
            [(c.source_id, c.title) for c in chapters],
            [
                ("leer/gato-3/", "Capítulo 3 - El giro"),
                ("leer/gato-2/", "Capítulo 2"),
                ("leer/gato-1/", "Capítulo 1"),
            ],
        )
        self.assertEqual(chapters[0].uploaded_at, "2026-08-05T00:00:00")

    async def test_el_id_del_manga_puede_venir_de_un_script(self):
        ficha = f'<script>var mnWpMangaId = 77;</script>{chapter_item("1", "gato-1")}'
        fetcher = Fetcher([
            Response(f"{BASE}/manga/el-gato/", ficha),
            Response(f"{BASE}/wp-admin/admin-ajax.php", {"data": {"html": "", "has_more": False}}),
        ])
        source = source_class()(fetcher)

        await source.chapters("manga/el-gato/")

        self.assertEqual(fetcher.requests[1][2]["data"]["manga_id"], "77")

    async def test_lector_prueba_los_selectores_en_orden(self):
        lector = '<div class="pg-box"><img data-lazy-src="/p/1.jpg"><img src="/p/2.jpg"></div>'
        fetcher = Fetcher([Response(f"{BASE}/leer/gato-3/", lector)])
        source = source_class()(fetcher)

        pages = await source.pages("leer/gato-3/")

        self.assertEqual([page.source_id for page in pages], [f"{BASE}/p/1.jpg", f"{BASE}/p/2.jpg"])


if __name__ == "__main__":
    unittest.main()
