from __future__ import annotations

import unittest
from pathlib import Path

from tools.generate import _madara_bundle, _supported_madara


class Response:
    def __init__(self, url, text):
        self.url, self.text, self.status_code = url, text, 200

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
    module = root.parent / "extensions-source-main" / "src" / "es" / "houseofotakus"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_madara(module, build)
    assert config is not None
    bundle = _madara_bundle((root / "engines" / "madara.py").read_text(encoding="utf-8"), config)
    namespace = {"__name__": "test_houseofotakus_bundle"}
    exec(compile(bundle, "houseofotakus_es.py", "exec"), namespace)
    return namespace["SOURCE"]


class HouseOfOtakusTest(unittest.IsolatedAsyncioTestCase):
    async def test_madara_overrides_use_ajax_listing_and_new_chapter_endpoint(self):
        listing = """
        <div class="page-item-detail"><div class="post-title">
          <a href="/manga/gato">Gato</a>
        </div><img src="/gato.jpg"></div>
        """
        series_page = '<div id="manga-chapters-holder-9" data-id="9"></div>'
        chapters_page = """
        <li class="wp-manga-chapter"><a href="/manga/gato/capitulo-2">Capitulo 2</a>
          <span class="chapter-release-date">agosto 04, 2026</span>
        </li>
        """
        fetcher = Fetcher([
            Response("https://houseofotakusv2.xyz/wp-admin/admin-ajax.php", listing),
            Response("https://houseofotakusv2.xyz/manga/gato", series_page),
            Response("https://houseofotakusv2.xyz/manga/gato/ajax/chapters", chapters_page),
        ])
        source = source_class()(fetcher)

        popular = await source.browse("popular", 3)
        chapters = await source.chapters("https://houseofotakusv2.xyz/manga/gato")

        self.assertEqual(fetcher.requests[0][0:2], (
            "POST", "https://houseofotakusv2.xyz/wp-admin/admin-ajax.php",
        ))
        self.assertEqual(fetcher.requests[0][2]["data"]["page"], "2")
        self.assertEqual(popular[0].title, "Gato")
        self.assertEqual(fetcher.requests[2][0:2], (
            "POST", "https://houseofotakusv2.xyz/manga/gato/ajax/chapters",
        ))
        self.assertEqual(chapters[0].source_id, (
            "https://houseofotakusv2.xyz/manga/gato/capitulo-2?style=list"
        ))
        self.assertEqual(chapters[0].uploaded_at, "2026-08-04T00:00:00")
        self.assertEqual((source.load_more, source.use_new_chapter_endpoint), ("always", True))
        self.assertEqual((source.date_format, source.date_locale), ("MMMM dd, yyyy", "es"))
        self.assertEqual(source.capabilities.content_warning, "mixed")


if __name__ == "__main__":
    unittest.main()
