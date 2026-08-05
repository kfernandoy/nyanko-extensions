from __future__ import annotations

import unittest
from pathlib import Path

from tools.generate import _manual_bundle


class Response:
    def __init__(self, url: str, text: str):
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
    path = Path(__file__).parents[1] / "engines" / "manual" / "comicfury_es.py"
    namespace = {"__name__": "test_comicfury_bundle"}
    exec(compile(_manual_bundle(path), str(path), "exec"), namespace)
    return namespace["SOURCE"]


class ComicFuryTest(unittest.IsolatedAsyncioTestCase):
    async def test_content_warning_is_accepted_and_filters_are_sent(self):
        warning = '<title>Content Warning</title><input name="token" value="abc"><input name="proceed" value="View Webcomic">'
        listing = '''<div class="webcomic-result">
            <div class="webcomic-result-avatar"><a href="?url=gato"><img src="/gato.jpg"></a></div>
            <div class="webcomic-result-title" title="Gato cosmico"></div>
        </div><div class="search-next-page"></div>'''
        fetcher = Fetcher([
            Response("https://comicfury.com/search.php", warning),
            Response("https://comicfury.com/search.php", listing),
        ])
        source = source_class()(fetcher)

        result = await source.search("gato", 2, {"tags": "humor, gatos", "completed": True})

        self.assertEqual(result["items"][0].title, "Gato cosmico")
        self.assertTrue(result["has_more"])
        self.assertEqual(fetcher.requests[0][2]["params"]["tags"], "humor,gatos")
        self.assertEqual(fetcher.requests[0][2]["params"]["completed"], "0")
        self.assertEqual(fetcher.requests[1][0], "POST")
        self.assertEqual(fetcher.requests[1][2]["data"], {"token": "abc", "proceed": "View Webcomic"})

    async def test_archive_pagination_and_author_note_page(self):
        first = '''<a href="/read/gato/1"><div class="archive-comic">
            <span class="archive-comic-title">Uno</span></div></a>
            <span class="vfpagecurrent">1</span><a class="vfpage" href="?page=2">2</a>'''
        second = '<a href="/read/gato/2"><div class="archive-comic"><span class="archive-comic-title">Dos</span></div></a>'
        reader = '''<div class="is--comic-page">
            <div class="is--image-segment"><div><img src="https://cdn/1.jpg"></div></div>
            <div class="is--author-notes"><div class="is--comment-box">
                <a class="is--comment-author">Ana</a><div class="is--comment-content">Gracias por leer.</div>
            </div></div>
        </div>'''
        fetcher = Fetcher([
            Response("https://comicfury.com/read/gato/archive", first),
            Response("https://comicfury.com/read/gato/archive?page=2", second),
            Response("https://comicfury.com/read/gato/2", reader),
        ])
        source = source_class()(fetcher)
        source.preferences = {"showAuthorsNotes": True}

        chapters = await source.chapters("https://comicfury.com/?url=gato")
        pages = await source.pages(chapters[0])
        note = await source.page_bytes(pages[1])

        self.assertEqual([chapter.title for chapter in chapters], ["Dos", "Uno"])
        self.assertEqual([chapter.number for chapter in chapters], [1.0, 0.0])
        self.assertEqual(pages[0].source_id, "https://cdn/1.jpg")
        self.assertEqual(note.media_type, "image/svg+xml")
        self.assertIn(b"Gracias por leer.", b"".join(note.chunks))


if __name__ == "__main__":
    unittest.main()
