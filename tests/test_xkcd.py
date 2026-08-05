from __future__ import annotations

import json
import unittest
from pathlib import Path
from urllib.parse import parse_qs

from tools.generate import _manual_bundle

ENGLISH_ARCHIVE = """
<div id="middleContainer">
  <a href="/2/" title="2026-1-9">Dos</a>
  <a href="/1/" title="2026-1-2">Uno</a>
</div>
"""


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


def source_class(extension_id: str):
    path = Path(__file__).parents[1] / "engines" / "manual" / f"{extension_id}.py"
    namespace = {"__name__": f"test_{extension_id}_bundle"}
    exec(compile(_manual_bundle(path), str(path), "exec"), namespace)
    return namespace["SOURCE"]


class XkcdTest(unittest.IsolatedAsyncioTestCase):
    async def test_ingles_lee_numero_y_fecha_del_archivo(self):
        fetcher = Fetcher([
            Response("https://xkcd.com/archive", ENGLISH_ARCHIVE),
            Response("https://xkcd.com/archive/", ENGLISH_ARCHIVE),
        ])
        source = source_class("xkcd_en")(fetcher)

        chapters = await source.chapters("SINGLE")

        self.assertEqual(fetcher.requests[0][1], "https://xkcd.com/archive")
        self.assertEqual(
            [(c.source_id, c.title, c.number, c.uploaded_at) for c in chapters],
            [("2/", "2: Dos", 2.0, "2026-01-09T00:00:00"), ("1/", "1: Uno", 1.0, "2026-01-02T00:00:00")],
        )

    async def test_espanol_cruza_la_fecha_con_el_archivo_ingles(self):
        archivo = """
        <div class="archive-entry"><a href="/strips/dos/">Dos</a><time>2026-01-09</time></div>
        <div class="archive-entry"><a href="/strips/geografia/">Geografía</a><time>1999-01-01</time></div>
        <div class="archive-entry"><a href="/strips/huerfano/">Huérfano</a><time>1900-01-01</time></div>
        """
        fetcher = Fetcher([
            Response("https://es.xkcd.com/archive", archivo),
            Response("https://xkcd.com/archive/", ENGLISH_ARCHIVE),
        ])
        source = source_class("xkcd_es")(fetcher)

        chapters = await source.chapters("SINGLE")

        # "geografia" tiene numero fijo; la tira sin fecha conocida se descarta.
        self.assertEqual(
            [(c.source_id, c.number) for c in chapters],
            [("strips/dos/", 2.0), ("strips/geografia/", 1472.0)],
        )

    async def test_chino_usa_el_json_de_tiras(self):
        fetcher = Fetcher([
            Response("https://xkcd.tw/api/strips.json", {"a": {"id": 2, "title": "Dos"}}),
            Response("https://xkcd.com/archive/", ENGLISH_ARCHIVE),
        ])
        source = source_class("xkcd_zh")(fetcher)

        chapters = await source.chapters("SINGLE")

        self.assertEqual(
            [(c.source_id, c.title, c.uploaded_at) for c in chapters],
            [("2", "2: Dos", "2026-01-09T00:00:00")],
        )

    async def test_frances_toma_el_numero_de_la_query_y_se_invierte(self):
        archivo = """
        <div id="content"><div class="s">
          <a href="/tous-episodes.php?num=1">Un</a>
          <a href="/tous-episodes.php?num=2">Deux</a>
          <a href="/suivant">Suivant</a>
        </div></div>
        """
        fetcher = Fetcher([
            Response("https://xkcd.lapin.org/tous-episodes.php", archivo),
            Response("https://xkcd.com/archive/", ENGLISH_ARCHIVE),
        ])
        source = source_class("xkcd_fr")(fetcher)

        chapters = await source.chapters("SINGLE")

        # El ultimo enlace del bloque no es una tira y la lista se invierte.
        self.assertEqual(
            [(c.source_id, c.number) for c in chapters],
            [("tous-episodes.php?num=2", 2.0), ("tous-episodes.php?num=1", 1.0)],
        )

    async def test_ruso_toma_el_titulo_del_alt(self):
        archivo = '<div class="main"><a href="/2/"><img alt="Два" src="/i/2.png"></a></div>'
        fetcher = Fetcher([
            Response("https://xkcd.ru/img", archivo),
            Response("https://xkcd.com/archive/", ENGLISH_ARCHIVE),
        ])
        source = source_class("xkcd_ru")(fetcher)

        chapters = await source.chapters("SINGLE")

        self.assertEqual([(c.source_id, c.title) for c in chapters], [("2/", "2: Два")])

    async def test_lector_devuelve_la_tira_y_la_pagina_de_texto(self):
        comic = """
        <div id="comic"><img src="/comics/dos.png" alt="Texto alternativo" title="Chiste oculto"></div>
        """
        fetcher = Fetcher([Response("https://xkcd.com/2/", comic)])
        source = source_class("xkcd_en")(fetcher)

        pages = await source.pages("2/")
        content = await source.page_bytes(pages[1])

        self.assertEqual(pages[0].source_id, "https://xkcd.com/comics/dos.png")
        self.assertTrue(pages[1].source_id.startswith("nyanko-text:"))
        values = parse_qs(pages[1].source_id[len("nyanko-text:"):])
        self.assertEqual(values["alt"], ["Texto alternativo"])
        self.assertEqual(values["title"], ["Chiste oculto"])
        # La tira de texto se dibuja: el Kotlin delega en TextInterceptor.
        self.assertEqual(content.media_type, "image/png")
        self.assertTrue(b"".join(content.chunks).startswith(b"\x89PNG\r\n\x1a\n"))

    async def test_catalogo_agrupa_en_una_sola_serie(self):
        comic = '<div id="comic"><img src="/comics/dos.png" alt="a" title="b"></div>'
        fetcher = Fetcher([
            Response("https://xkcd.com/archive", ENGLISH_ARCHIVE),
            Response("https://xkcd.com/archive/", ENGLISH_ARCHIVE),
            Response("https://xkcd.com/2/", comic),
        ])
        source = source_class("xkcd_en")(fetcher)

        result = await source.browse("popular")

        self.assertEqual([item.source_id for item in result["items"]], ["SINGLE"])
        self.assertEqual(result["items"][0].title, "xkcd")
        self.assertEqual(result["items"][0].author, "Randall Munroe")
        self.assertEqual(result["items"][0].cover_url, "https://xkcd.com/comics/dos.png")
        self.assertFalse(result["has_more"])
        self.assertFalse(source.supports_latest)

    async def test_la_busqueda_no_esta_implementada(self):
        source = source_class("xkcd_en")(Fetcher([]))

        self.assertEqual(await source.search("gato"), {"items": [], "has_more": False})


if __name__ == "__main__":
    unittest.main()
