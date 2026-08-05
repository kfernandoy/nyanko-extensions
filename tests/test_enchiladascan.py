from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.generate import _extract_kotlin_metadata, _generic_bundle, _supported_generic


class Response:
    def __init__(self, url, payload="", content=b"", status=200, headers=None):
        self.url, self.payload, self.status_code = url, payload, status
        self.text = payload if isinstance(payload, str) else json.dumps(payload)
        self.content, self.headers = content, headers or {}

    def json(self):
        return self.payload if not isinstance(self.payload, str) else json.loads(self.payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise ValueError(self.status_code)


class Fetcher:
    def __init__(self, responses):
        self.responses, self.requests = responses, []

    async def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return self.responses.pop(0)


def source_class():
    root = Path(__file__).parents[1]
    module = root.parent / "extensions-source-main" / "src" / "es" / "enchiladascan"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_generic(module, build)
    assert config is not None
    config["content_warning"] = _extract_kotlin_metadata(module)
    bundle = _generic_bundle(
        (root / "engines" / "madara.py").read_text(encoding="utf-8"),
        (root / "engines" / "generic.py").read_text(encoding="utf-8"),
        config,
    )
    namespace = {"__name__": "test_enchiladascan_bundle"}
    exec(compile(bundle, "enchiladascan_es.py", "exec"), namespace)
    return namespace["SOURCE"]


class EnchiladaScanTest(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_is_cached_and_searches_titles_in_memory(self):
        catalog = {"items": [
            {"title": "Dragón Rojo", "post_url": "/manga/dragon/", "portada": "/assets/dragon.jpg"},
            {"title": "Taco", "post_url": "/manga/taco/", "portada": "/assets/taco.jpg"},
        ]}
        fetcher = Fetcher([Response(
            "https://enchiladascan.github.io/enchiladaweb/catalogo.json", catalog,
        )])
        source = source_class()(fetcher)

        popular = await source.browse("popular", 9)
        search = await source.search("DRAGÓN", 4)
        latest = await source.browse("latest")

        self.assertEqual(len(fetcher.requests), 1)
        self.assertEqual(fetcher.requests[0][1], "https://enchiladascan.github.io/enchiladaweb/catalogo.json")
        self.assertEqual([item.title for item in popular["items"]], ["Dragón Rojo", "Taco"])
        self.assertEqual(search["items"][0].source_id, "/manga/dragon/")
        self.assertEqual(search["items"][0].cover_url, "https://enchiladascan.github.io/enchiladaweb/assets/dragon.jpg")
        self.assertEqual(search["items"][0].web_url, "https://enchiladascan.github.io/enchiladaweb/manga/dragon/")
        self.assertEqual(latest, {"items": [], "has_more": False})
        self.assertEqual(source.capabilities.headers["Referer"], "https://enchiladascan.github.io/enchiladaweb/")
        self.assertEqual(source.capabilities.content_warning, "safe")

    async def test_reversed_chapters_derived_manifest_and_image_referer_match_kotlin(self):
        details = '''<ul id="chaptersList">
            <li><a href="https://enchiladascan.github.io/enchiladaweb/manga/taco/cap-1/"><span class="cap-title">Capitulo 1</span></a></li>
            <li><a href="/enchiladaweb/manga/taco/cap-2/"><span class="cap-title">Capitulo 2</span></a></li>
            <div><li><a href="/ignorado"><span class="cap-title">Ignorado</span></a></li></div>
        </ul>'''
        images = ["https://cdn.example/1.webp", "https://cdn.example/2.webp"]
        fetcher = Fetcher([
            Response("https://enchiladascan.github.io/enchiladaweb/manga/taco/", details),
            Response("https://enchiladascan.github.io/enchiladaweb/assets/mangas/taco/cap-2/images.json", images),
            Response("https://cdn.example/1.webp", content=b"image", headers={"Content-Type": "image/webp"}),
        ])
        source = source_class()(fetcher)

        chapters = await source.chapters("/manga/taco/")
        pages = await source.pages(chapters[0])
        content = await source.page_bytes(pages[0])

        self.assertEqual([chapter.title for chapter in chapters], ["Capitulo 2", "Capitulo 1"])
        self.assertEqual(chapters[0].source_id, "/enchiladaweb/manga/taco/cap-2/")
        self.assertEqual(fetcher.requests[1][1], "https://enchiladascan.github.io/enchiladaweb/assets/mangas/taco/cap-2/images.json")
        self.assertEqual([page.source_id for page in pages], images)
        self.assertEqual(fetcher.requests[2][2]["headers"]["Referer"], "https://enchiladascan.github.io/enchiladaweb/")
        self.assertEqual(content.media_type, "image/webp")
        self.assertEqual(b"".join(content.chunks), b"image")


if __name__ == "__main__":
    unittest.main()
