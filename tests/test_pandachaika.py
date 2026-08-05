from __future__ import annotations

import io
import json
import unittest
import zipfile
from pathlib import Path
from urllib.parse import parse_qs

from tools.generate import _manual_bundle

BASE = "https://panda.chaika.moe"


class Response:
    def __init__(self, url: str, payload=None, content: bytes = b"") -> None:
        self.url = url
        self.text = payload if isinstance(payload, str) else json.dumps(payload or {})
        self.content = content
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


class ZipFetcher:
    """Sirve rangos de un ZIP en memoria, como haria el CDN."""

    def __init__(self, blob: bytes) -> None:
        self.blob = blob
        self.ranges: list[str] = []

    async def request(self, method: str, url: str, **kwargs):
        value = kwargs["headers"]["Range"].removeprefix("bytes=")
        self.ranges.append(value)
        if value.startswith("-"):
            return Response(url, content=self.blob[-int(value[1:]):])
        start, _, end = value.partition("-")
        return Response(url, content=self.blob[int(start) : int(end) + 1])


def source_class(extension_id: str = "pandachaika_es"):
    path = Path(__file__).parents[1] / "engines" / "manual" / f"{extension_id}.py"
    namespace = {"__name__": f"test_{extension_id}_bundle"}
    exec(compile(_manual_bundle(path), str(path), "exec"), namespace)
    return namespace["SOURCE"]


def archive_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as handle:
        handle.writestr("02.png", b"segunda pagina" * 40)
        handle.writestr("01.jpg", b"primera pagina" * 40)
    return buffer.getvalue()


ARCHIVE = {
    "id": 7, "title": "El Archivo", "title_jpn": "アーカイブ",
    "thumbnail": "https://cdn/t.jpg", "posted": 1754352000, "public_date": 1754265600,
    "filecount": 20, "filesize": 5_000_000.0, "uploader": "",
    "tags": ["artist:kim_lee", "group:equipo", "female:romance", "male:action", ":sin_categoria"],
}


class PandaChaikaTest(unittest.IsolatedAsyncioTestCase):
    async def test_catalogos_ordenan_por_rating_y_fecha(self):
        payload = {"archives": [ARCHIVE], "has_next": True}
        fetcher = Fetcher([
            Response(f"{BASE}/search/", payload),
            Response(f"{BASE}/search/", payload),
        ])
        source = source_class()(fetcher)

        popular = await source.browse("popular", 2)
        await source.browse("latest", 1)

        self.assertEqual(fetcher.requests[0][2]["params"], [
            ("tags", "spanish"), ("sort", "rating"), ("apply", ""), ("json", ""), ("page", "2"),
        ])
        self.assertEqual(fetcher.requests[1][2]["params"][1], ("sort", "public_date"))
        self.assertEqual(popular["items"][0].source_id, "7")
        self.assertTrue(popular["has_more"])

    async def test_la_ficha_arma_la_descripcion_desde_las_etiquetas(self):
        payload = {"archives": [ARCHIVE], "has_next": False}
        fetcher = Fetcher([Response(f"{BASE}/search/", payload)])
        source = source_class()(fetcher)

        manga = (await source.browse("popular"))["items"][0]

        self.assertEqual((manga.author, manga.artist), ("Equipo", "Kim Lee"))
        self.assertIn("Uploader: Anonymous", manga.description)
        self.assertIn("Japanese Title: アーカイブ", manga.description)
        self.assertIn("File Size: 5.00 MB", manga.description)
        self.assertIn("Male tags: Action", manga.description)
        # Las etiquetas sin categoria empiezan por ":" y caen en "otras".
        self.assertIn("Other tags: Sin Categoria", manga.description)
        self.assertEqual(manga.content_tags, ("Action", "Romance", "Sin Categoria"))

    async def test_busqueda_prefija_cada_etiqueta_con_su_tipo(self):
        fetcher = Fetcher([Response(f"{BASE}/search/", {"archives": [], "has_next": False})])
        source = source_class()(fetcher)

        await source.search("gato", 2, {
            "female_tags": "romance, -drama", "artists": "kim", "pages": ">=30",
            "category": "Manga", "asc_desc": "asc", "reason": "motivo",
        })

        params = dict(fetcher.requests[0][2]["params"])
        self.assertEqual(params["tags"], "spanish, female:romance, -female:drama, artist:kim")
        self.assertEqual((params["filecount_from"], params["filecount_to"]), ("30", "9999"))
        self.assertEqual((params["category"], params["asc_desc"]), ("Manga", "asc"))
        self.assertEqual(params["reason"], "motivo")
        # El filtro Uploader del Kotlin declara el tipo "reason": nunca viaja.
        self.assertEqual(params["uploader"], "")

    async def test_busqueda_por_id_valida_contra_el_listado(self):
        fetcher = Fetcher([
            Response(f"{BASE}/api", {"title": "El Archivo", "posted": 0, "download": "/x"}),
            Response(f"{BASE}/search/", {"archives": [ARCHIVE], "has_next": False}),
        ])
        source = source_class()(fetcher)

        result = await source.search("id:7")

        self.assertEqual(fetcher.requests[0][2]["params"], {"archive": "7"})
        self.assertEqual(fetcher.requests[1][2]["params"], [("qsearch", "El Archivo"), ("json", "")])
        self.assertEqual([item.source_id for item in result["items"]], ["7"])

    async def test_capitulo_unico_desde_la_api(self):
        fetcher = Fetcher([
            Response(f"{BASE}/api", {
                "download": "/archive/7/download/", "posted": 1754352000, "title": "El Archivo",
            }),
        ])
        source = source_class()(fetcher)

        chapters = await source.chapters("7")

        self.assertEqual(len(chapters), 1)
        self.assertEqual(chapters[0].source_id, "archive/7")
        self.assertEqual((chapters[0].title, chapters[0].uploaded_at), ("Chapter", "2025-08-05T00:00:00"))

    async def test_el_lector_lee_el_zip_remoto_por_rangos(self):
        blob = archive_zip()
        fetcher = ZipFetcher(blob)
        source = source_class()(fetcher)

        pages = await source.pages("archive/7")
        first = await source.page_bytes(pages[0])
        second = await source.page_bytes(pages[1])

        # Las entradas se ordenan por nombre sin distinguir mayusculas.
        self.assertEqual([page.filename for page in pages], ["01.jpg", "02.png"])
        self.assertTrue(pages[0].source_id.startswith("nyanko-zip:"))
        values = parse_qs(pages[0].source_id[len("nyanko-zip:"):])
        self.assertEqual(values["u"], [f"{BASE}/archive/7/download/"])
        # La primera peticion es el sufijo con el directorio central.
        self.assertTrue(fetcher.ranges[0].startswith("-"))
        self.assertEqual(first.media_type, "image/jpeg")
        self.assertEqual(b"".join(first.chunks), b"primera pagina" * 40)
        self.assertEqual(second.media_type, "image/png")
        self.assertEqual(b"".join(second.chunks), b"segunda pagina" * 40)


if __name__ == "__main__":
    unittest.main()
