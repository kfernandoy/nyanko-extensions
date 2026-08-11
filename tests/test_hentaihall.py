from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.generate import _extract_kotlin_metadata, _hentaihall_bundle, _supported_hentaihall


class Response:
    def __init__(self, url, payload=None, *, content=b"", headers=None):
        self.url, self.status_code = url, 200
        self.payload = payload
        self.text = payload if isinstance(payload, str) else json.dumps(payload or {})
        self.content = content
        self.headers = headers or {}

    def json(self):
        return self.payload

    def raise_for_status(self):
        pass


class Fetcher:
    def __init__(self, responses):
        self.responses, self.requests = responses, []

    async def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return self.responses.pop(0)


def source_class():
    root = Path(__file__).parents[1]
    module = root.parent / "extensions-source-main" / "src" / "es" / "hentaihall"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_hentaihall(module, build)
    assert config is not None
    config["content_warning"] = _extract_kotlin_metadata(module)
    bundle = _hentaihall_bundle(
        (root / "engines" / "base.py").read_text(encoding="utf-8"),
        (root / "engines" / "hentaihall.py").read_text(encoding="utf-8"),
        config,
    )
    namespace = {"__name__": "test_hentaihall_bundle"}
    exec(compile(bundle, "hentaihall_es.py", "exec"), namespace)
    return namespace["SOURCE"]


def library(identifier="gato", title="Gato", next_page=False):
    return {"data": [{"_id": identifier, "nombre": title, "imagen": f"https://cdn/{identifier}.jpg"}], "next": next_page}


class HentaiHallTest(unittest.IsolatedAsyncioTestCase):
    async def test_library_modes_filters_and_zero_based_pages_match_kotlin(self):
        fetcher = Fetcher([
            Response("https://api/library", library(next_page=True)),
            Response("https://api/library", library("perro", "Perro")),
            Response("https://api/library", library("zorro", "Zorro")),
        ])
        source = source_class()(fetcher)

        popular = await source.browse("popular", 2)
        latest = await source.browse("latest", 1)
        search = await source.search("ana", 3, {
            "search_by": "autores", "sort": "alfabetico", "direction": "asc",
            "genres": ["Anal", "Yuri"],
        })

        self.assertEqual(fetcher.requests[0][2]["params"], {
            "buscar": "", "quebusca": "nombre", "order_item": "seguir",
            "order_dir": "desc", "page": "1", "generes": "",
        })
        self.assertEqual(fetcher.requests[1][2]["params"]["order_item"], "creacion")
        self.assertEqual(fetcher.requests[2][2]["params"], {
            "buscar": "ana", "quebusca": "autores", "order_item": "alfabetico",
            "order_dir": "asc", "page": "2", "generes": "Anal_Yuri",
        })
        self.assertTrue(popular["has_more"])
        self.assertEqual(latest["items"][0].web_url, "https://hentaihall.com/content/perro")
        self.assertEqual(search["items"][0].title, "Zorro")
        filters = source.get_filters()
        self.assertEqual([item.id for item in filters], ["search_by", "sort", "direction", "genres"])
        self.assertEqual((filters[0].default, filters[1].default, filters[2].default), ("nombre", "seguir", "desc"))
        self.assertEqual(filters[3].options[-1], ("3D", "3D"))
        self.assertEqual(source.capabilities.content_warning, "nsfw")
        self.assertEqual(source.capabilities.headers["Origin"], "https://hentaihall.com")

    async def test_details_and_single_chapter_share_the_see_endpoint(self):
        payload = {
            "_id": "gato", "nombre": "Gato", "imagen": "https://cdn/gato.jpg",
            "tags": ["Anal", "Romance"], "autores": ["Ana", "Leo"],
            "tipo": "manhwa", "creacion": "2026-08-04T10:11:12.123Z",
            "name_grupo": "Hall", "lenguaje": "esp",
        }
        fetcher = Fetcher([
            Response("https://api/see/gato", payload),
            Response("https://api/see/gato", payload),
        ])
        source = source_class()(fetcher)

        manga = await source.details("gato")
        chapters = await source.chapters("gato")

        expected = "https://hentaihallbackend-production.up.railway.app/manhwa/see/gato"
        self.assertEqual([request[1] for request in fetcher.requests], [expected, expected])
        self.assertEqual((manga.author, manga.artist, manga.status), ("Ana, Leo", "Ana, Leo", "completed"))
        self.assertEqual(manga.description, "Tipo: Manhwa\nLenguaje: Español\nGrupo: Hall")
        self.assertEqual(manga.content_tags, ("Anal", "Romance"))
        self.assertEqual(chapters[0].source_id, "gato")
        self.assertEqual(chapters[0].title, "Chapter")
        self.assertEqual(chapters[0].uploaded_at, "2026-08-04T10:11:12.123000+00:00")

    async def test_pages_skip_blanks_and_images_request_original_quality(self):
        fetcher = Fetcher([
            Response("https://api/chapter/gato", {"chapter": ["https://cdn/1.jpg", " ", "https://cdn/2.jpg"]}),
            Response("https://cdn/1.jpg", content=b"jpeg", headers={"Content-Type": "image/jpeg"}),
        ])
        source = source_class()(fetcher)

        pages = await source.pages("gato")
        content = await source.page_bytes(pages[0])

        self.assertEqual(fetcher.requests[0][1], "https://hentaihallbackend-production.up.railway.app/manhwa/chapter/gato")
        self.assertEqual([page.source_id for page in pages], ["https://cdn/1.jpg", "https://cdn/2.jpg"])
        self.assertEqual(fetcher.requests[1][2]["headers"]["Accept"], "*/*")
        self.assertEqual(b"".join(content.chunks), b"jpeg")


if __name__ == "__main__":
    unittest.main()
