from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.generate import _manual_bundle

BASE = "https://onisaga.com"


class Response:
    def __init__(self, url: str, payload=None, headers: dict | None = None, code: int = 200) -> None:
        self.url = url
        self.text = payload if isinstance(payload, str) else json.dumps(payload or {})
        self.headers = headers or {}
        self.status_code = code
        self.content = b"binario"

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


def source_class(extension_id: str = "onisaga_es"):
    path = Path(__file__).parents[1] / "engines" / "manual" / f"{extension_id}.py"
    namespace = {"__name__": f"test_{extension_id}_bundle"}
    exec(compile(_manual_bundle(path), str(path), "exec"), namespace)
    source = namespace["SOURCE"]
    source.image_delay_seconds = 0.0
    return source


BROWSE = """
<meta name="csrf-token" content="tok3n">
<div wire:snapshot='{"memo":{"name":"post-filter"}}'></div>
<div class="relative group"><a href="/manga/gato"><img alt="Gato" src="/gato.jpg"></a>
  <div data-flux-heading>Gato</div></div>
<div class="relative group"><span>18+</span>
  <a href="/manga/adulto"><img alt="Adulto" src="/a.jpg"></a>
  <div data-flux-heading>Adulto</div></div>
<button wire:click="nextPage">Siguiente</button>
"""


def livewire(html: str, snapshot: str = "snap2") -> dict:
    return {"components": [{"effects": {"html": html}, "snapshot": snapshot}]}


class OniSagaTest(unittest.IsolatedAsyncioTestCase):
    async def test_populares_pasan_por_livewire_y_ocultan_18(self):
        fetcher = Fetcher([
            Response(f"{BASE}/browse", BROWSE),
            Response(f"{BASE}/livewire/update", livewire(BROWSE)),
        ])
        source = source_class()(fetcher)

        result = await source.browse("popular", 2)

        self.assertEqual(fetcher.requests[1][0], "POST")
        self.assertEqual(fetcher.requests[1][1], f"{BASE}/livewire/update")
        body = fetcher.requests[1][2]["json"]
        self.assertEqual(body["_token"], "tok3n")
        component = body["components"][0]
        self.assertEqual(component["updates"]["sort"], "view")
        self.assertEqual(component["calls"], [
            {"type": "call", "path": "", "method": "gotoPage", "params": ["2"]},
        ])
        self.assertEqual(fetcher.requests[1][2]["headers"]["X-Livewire"], "")
        # La tarjeta marcada 18+ no aparece: la preferencia no vuelve a la fuente.
        self.assertEqual([item.source_id for item in result["items"]], ["gato"])
        self.assertTrue(result["has_more"])

    async def test_busqueda_sin_filtros_lee_la_primera_pagina_directa(self):
        fetcher = Fetcher([Response(f"{BASE}/search/gato", BROWSE)])
        source = source_class()(fetcher)

        result = await source.search("gato", 1, {})

        # Sin filtros y en la pagina 1 el Kotlin no llama a Livewire.
        self.assertEqual(len(fetcher.requests), 1)
        self.assertEqual(fetcher.requests[0][1], f"{BASE}/search/gato")
        self.assertEqual([item.source_id for item in result["items"]], ["gato"])

    async def test_los_filtros_arman_el_dto_de_livewire(self):
        fetcher = Fetcher([
            Response(f"{BASE}/browse", BROWSE),
            Response(f"{BASE}/livewire/update", livewire(BROWSE)),
        ])
        source = source_class()(fetcher)

        await source.search("", 1, {
            "platform": "MANHWA", "sort": "title", "min_chapters": "50",
            "genre": {"1": "include", "16": "exclude"}, "group": " equipo ",
        })

        updates = fetcher.requests[1][2]["json"]["components"][0]["updates"]
        self.assertEqual(updates["platform"], "MANHWA")
        self.assertEqual(updates["sort"], "title")
        self.assertEqual(updates["min_chapters"], "50")
        self.assertEqual((updates["genre"], updates["excludeGenre"]), (["1"], ["16"]))
        self.assertEqual(updates["group"], "equipo")
        self.assertIsNone(updates["release_start"])

    async def test_capitulos_recorren_los_idiomas_y_los_desplegables(self):
        chapter_state = """
        <meta name="csrf-token" content="tok3n">
        <div wire:snapshot='{"memo":{"name":"manga.chapter-list"}}'></div>
        """
        listado = """
        <a class="gap-4" href="/read/gato-1"><div data-flux-heading>Chapter 1</div>
          <p data-flux-text>Equipo · 2 days ago</p></a>
        <ui-dropdown><button><div data-flux-heading>Chapter 2</div>
            <p data-flux-text>3 hours ago</p></button>
          <ui-menu>
            <a data-flux-menu-item href="/read/gato-2a"><span class="text-sm">Alfa</span></a>
            <a data-flux-menu-item href="/read/gato-2b"><span class="text-sm">Unknown group</span></a>
          </ui-menu></ui-dropdown>
        """
        fetcher = Fetcher([
            Response(f"{BASE}/manga/gato", chapter_state),
            Response(f"{BASE}/livewire/update", livewire(listado)),
            Response(f"{BASE}/livewire/update", livewire(listado, "")),
        ])
        source = source_class()(fetcher)

        chapters = await source.chapters("gato")

        self.assertEqual(
            fetcher.requests[1][2]["json"]["components"][0]["updates"], {"language": "ES"},
        )
        self.assertEqual(
            [(c.source_id, c.title, c.scanlator) for c in chapters],
            [
                ("read/gato-2a", "Chapter 2", "Alfa"),
                ("read/gato-2b", "Chapter 2", "Unknown 1"),
                ("read/gato-1", "Chapter 1", ""),
            ],
        )
        self.assertIsNotNone(chapters[2].uploaded_at)

    async def test_la_fuente_all_recorre_los_siete_idiomas(self):
        chapter_state = """
        <meta name="csrf-token" content="tok3n">
        <div wire:snapshot='{"memo":{"name":"manga.chapter-list"}}'></div>
        """
        vacio = livewire("")
        fetcher = Fetcher([Response(f"{BASE}/manga/gato", chapter_state)] + [
            Response(f"{BASE}/livewire/update", vacio) for _ in range(7)
        ])
        source = source_class("onisaga_all")(fetcher)

        await source.chapters("gato")

        codes = [
            request[2]["json"]["components"][0]["updates"]["language"]
            for request in fetcher.requests[1:]
        ]
        self.assertEqual(codes, ["EN", "FR", "JA", "PT-BR", "PT", "ES-LA", "ES"])

    async def test_paginas_cuentan_los_order_y_resuelven_la_imagen(self):
        lector = """<script>window.reader = {readerToken: "abc", pages:[
          {order: 0}, {order: 1}
        ]}</script>"""
        fetcher = Fetcher([
            Response(f"{BASE}/read/gato-1", lector),
            Response(f"{BASE}/api/chapter/gato-1/page/1", {"url": "https://cdn/1.jpg"},
                     {"x-reader-token-next": "def"}),
            Response("https://cdn/1.jpg", "", {"Content-Type": "image/webp"}),
        ])
        source = source_class()(fetcher)

        pages = await source.pages("read/gato-1")
        content = await source.page_bytes(pages[1])

        self.assertEqual([page.source_id for page in pages], ["read/gato-1#0", "read/gato-1#1"])
        self.assertEqual(fetcher.requests[1][1], f"{BASE}/api/chapter/gato-1/page/1")
        self.assertEqual(fetcher.requests[1][2]["headers"]["X-Reader-Token"], "abc")
        # El servidor rota el token y la siguiente peticion debe usar el nuevo.
        self.assertEqual(source._reader_token, "def")
        self.assertEqual(fetcher.requests[2][2]["headers"]["Referer"], f"{BASE}/read/gato-1")
        self.assertEqual(content.media_type, "image/webp")


if __name__ == "__main__":
    unittest.main()
