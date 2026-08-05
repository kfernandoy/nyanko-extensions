from __future__ import annotations

import base64
import json
import unittest
from pathlib import Path
from urllib.parse import quote

from tools.generate import _generic_bundle, _supported_generic


class Response:
    def __init__(self, url, text="", payload=None):
        self.url, self.text, self.payload, self.status_code = url, text, payload, 200

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class Fetcher:
    def __init__(self, responses):
        self.responses, self.requests = responses, []

    async def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return self.responses.pop(0)


def source_class():
    root = Path(__file__).parents[1]
    module = root.parent / "extensions-source-main" / "src" / "es" / "mangasin"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_generic(module, build)
    assert config is not None
    config["content_warning"] = "mixed"
    bundle = _generic_bundle(
        (root / "engines" / "madara.py").read_text(encoding="utf-8"),
        (root / "engines" / "generic.py").read_text(encoding="utf-8"),
        config,
    )
    namespace = {"__name__": "test_mangasin_bundle"}
    exec(compile(bundle, "mangasin_es.py", "exec"), namespace)
    return namespace["SOURCE"]


class MangasInTest(unittest.IsolatedAsyncioTestCase):
    async def test_latest_encrypted_chapters_and_encoded_pages(self):
        encrypted = {
            "ct": "LMJHzDvyz9o+X8tQjXPYCKUV0CKUgm0DUrk/lrPVTJu99hS4099WlVWDZ5uB/ywxECZdycZbM+yRKQd1m+Yiiw8F+odq9/6xw903OJ9L44Y5iDWnBFg0kKg1s22xHtld",
            "iv": "",
            "s": "0011223344556677",
        }
        chapter_html = "<script>const chapters = '" + json.dumps(encrypted).replace('"', r'\"') + "';</script>"
        key_script = "CryptoJS.AES.decrypt(data, 'X^Ib1O*HLVh%3W2t', options);"
        image_url = "https://cdn.example/1.jpg?token=a"
        encoded_image = base64.b64encode(quote(image_url).encode()).decode()
        pages_html = f'<div id="all"><img class="img-responsive" data-src="encoded://{encoded_image}"></div>'
        fetcher = Fetcher([
            Response("https://m440.in/lasted?p=1", payload={
                "data": [{"manga_name": "Gato", "manga_slug": "gato"}], "totalPages": 2,
            }),
            Response("https://m440.in/manga/gato", chapter_html),
            Response("https://m440.in/js/ads2.js", key_script),
            Response("https://m440.in/manga/gato/capitulo-2", pages_html),
        ])
        source = source_class()(fetcher)

        latest = await source.browse("latest")
        chapter = (await source.chapters(latest["items"][0]))[0]
        pages = await source.pages(chapter)

        self.assertTrue(latest["has_more"])
        self.assertEqual(latest["items"][0].cover_url, "https://m440.in/uploads/manga/gato/cover/cover_250x350.jpg")
        self.assertEqual((chapter.title, chapter.number), ("Capítulo 2", 2.0))
        self.assertEqual(chapter.uploaded_at, "2026-08-05T12:30:00")
        self.assertEqual(pages[0].source_id, image_url)
        self.assertEqual(source.capabilities.headers["Referer"], "https://m440.in/")
        self.assertEqual(source.capabilities.content_warning, "mixed")


if __name__ == "__main__":
    unittest.main()
