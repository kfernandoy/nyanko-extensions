from __future__ import annotations

import base64
import unittest
from pathlib import Path

from tools.generate import _mangathemesia_bundle, _supported_mangathemesia


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
    module = root.parent / "extensions-source-main" / "src" / "es" / "mangatv"
    build = (module / "build.gradle.kts").read_text(encoding="utf-8")
    config = _supported_mangathemesia(module, build)
    assert config is not None
    bundle = _mangathemesia_bundle(
        (root / "engines" / "madara.py").read_text(encoding="utf-8"),
        (root / "engines" / "mangathemesia.py").read_text(encoding="utf-8"),
        config,
    )
    namespace = {"__name__": "test_mangatv_bundle"}
    exec(compile(bundle, "mangatv_es.py", "exec"), namespace)
    return namespace["SOURCE"]


class MangaTVTest(unittest.IsolatedAsyncioTestCase):
    async def test_paged_catalog_details_chapters_and_packed_pages(self):
        listing = '''<div class="listupd"><div class="bs"><div class="bsx">
          <a href="/manga/7/gato" title="Gato"><img data-src="/gato.jpg"></a>
        </div></div></div>'''
        details = '''<div class="postbody"><div class="bixbox animefull">
          <h1 class="entry-title">Gato</h1><div class="thumb"><img src="//img.example/gato.jpg"></div>
          <div class="imptdt">Estado <i>Finalizado</i></div>
          <div class="imptdt">Tipo <a>Manga</a></div>
          <div class="wd-full"><b>Sinopsis</b><span>Una historia.</span></div>
          <div class="wd-full"><b>Generos:</b><span><a>acción</a></span></div>
          <div id="chapterlist"><ul class="clstyle"><li>
            <div class="eph-num"><span class="chapternum">Capítulo 2</span>
            <span class="chapternum">El regreso</span><span class="chapterdate">2026-08-05</span></div>
            <div class="dt"><a href="/leer/abc"></a></div>
          </li></ul></div></div></div>'''
        encoded = base64.b64encode(b"//img.example/001.webp").decode()
        packed = """eval(function(p,a,c,k,e,d){e=function(c){return c.toString(a)};while(c--){if(k[c]){p=p.replace(new RegExp('\\\\b'+e(c)+'\\\\b','g'),k[c])}}return p}('0={\"1\":[\"%s\",],};',2,2,'data|images'.split('|'),0,{}))""" % encoded
        reader = f"<script>{packed}</script>"
        fetcher = Fetcher([
            Response("https://mangatv.net/lista", listing),
            Response("https://mangatv.net/lista", listing),
            Response("https://mangatv.net/manga/7/gato", details),
            Response("https://mangatv.net/manga/7/gato", details),
            Response("https://mangatv.net/leer/abc", reader),
        ])
        source = source_class()(fetcher)

        searched = (await source.search("gato", page=2))[0]
        browsed = (await source.browse("popular", page=3))[0]
        full = await source.details(searched)
        chapter = (await source.chapters(searched))[0]
        page = (await source.pages(chapter))[0]

        self.assertEqual(fetcher.requests[0][2]["params"], {"s": "gato", "page": "2"})
        self.assertEqual(fetcher.requests[1][2]["params"], {"s": "", "page": "3"})
        self.assertEqual((browsed.title, browsed.cover_url), ("Gato", "https://mangatv.net/gato.jpg"))
        self.assertEqual((full.description, full.status, full.content_tags), ("Una historia.", "completed", ("Acción", "Manga")))
        self.assertEqual((chapter.title, chapter.number, chapter.uploaded_at), ("Capítulo 2 El regreso", 2.0, "2026-08-05T00:00:00"))
        self.assertEqual(page.source_id, "https://img.example/001.webp")
        self.assertEqual((source.date_format, source.capabilities.content_warning), ("yyyy-MM-dd", "mixed"))


if __name__ == "__main__":
    unittest.main()
