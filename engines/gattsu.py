"""Implementación común de los sitios Gattsu."""

import re
from urllib.parse import urljoin

try:
    from .base import (
        FuenteBaseSource,
        SourceChapter,
        SourcePage,
        SourceSeries,
        _first,
        _image_url,
        _parse_html,
    )
except ImportError:
    pass


def _inside(node, class_name: str) -> bool:
    while node.parent is not None:
        node = node.parent
        if node.has_class(class_name):
            return True
    return False


class GattsuSource(FuenteBaseSource):
    profile = "regular"
    requests_per_minute = 30

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        response = await self._request(
            "GET",
            f"{self.base_url}/page/1/",
            params={"s": query.strip(), "post_type": "post"},
        )
        response.raise_for_status()
        return self._listing(response.text, str(response.url))[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"}:
            return []
        path = "" if page == 1 else f"page/{page}"
        response = await self._request("GET", f"{self.base_url}/{path}")
        response.raise_for_status()
        return self._listing(response.text, str(response.url))

    def _listing(self, html: str, response_url: str) -> list[SourceSeries]:
        root = _parse_html(html)
        result: list[SourceSeries] = []
        for anchor in root.descendants("a"):
            href = anchor.attrs.get("href", "")
            if not href or not urljoin(response_url, href).startswith(self.base_url):
                continue
            if self.profile == "universo":
                if not _inside(anchor, "videos") or _first(
                    anchor, lambda node: node.has_class("selo-hd")
                ):
                    continue
                title_node = _first(anchor, lambda node: node.has_class("video-titulo"))
            else:
                if not _inside(anchor, "lista"):
                    continue
                title_node = _first(anchor, lambda node: node.has_class("thumb-titulo"))
            title = title_node.text().strip() if title_node else ""
            if title:
                result.append(
                    SourceSeries(
                        source_id=urljoin(response_url, href),
                        title=title,
                        source_name=self.name,
                    )
                )
        return result

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        response = await self._request("GET", series_id)
        response.raise_for_status()
        root = _parse_html(response.text)
        chapter_url = str(response.url)
        if self.profile == "universo":
            gallery = _first(
                root,
                lambda node: node.tag == "a"
                and node.attrs.get("title") == "Abrir galeria"
                and bool(node.attrs.get("href")),
            )
            if gallery is None:
                return []
            chapter_url = urljoin(str(response.url), gallery.attrs["href"])
        elif not self._page_urls(root, str(response.url)):
            return []
        return [
            SourceChapter(
                source_id=chapter_url,
                title="Capítulo único",
                series_id=series_id,
                source_name=self.name,
                number=1,
            )
        ]

    def _page_urls(self, root, response_url: str) -> list[str]:
        result: list[str] = []
        for image in root.descendants("img"):
            if self.profile == "universo":
                selected = _inside(image, "galeria-foto")
            else:
                selected = _inside(image, "post-fotos") or _inside(image, "galeriaHtml")
            if selected and (url := _image_url(image, response_url)):
                result.append(re.sub(r"-\d+x\d+\.", ".", url))
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        response = await self._request("GET", chapter_id)
        response.raise_for_status()
        urls = self._page_urls(_parse_html(response.text), str(response.url))
        return [
            SourcePage(
                source_id=url,
                chapter_id=chapter_id,
                index=index,
                filename=url.rsplit("/", 1)[-1].split("?", 1)[0] or f"{index}.jpg",
                source_name=self.name,
            )
            for index, url in enumerate(urls, 1)
        ]
