"""Implementación común de sitios ManhwaZ para Nyanko Source v3."""

import re
from urllib.parse import urljoin

try:
    from .madara import (
        MadaraSource,
        SourceChapter,
        SourcePage,
        SourceSeries,
        _first,
        _image_url,
        _parse_html,
    )
except ImportError:
    pass


class ManhwaZSource(MadaraSource):
    search_path = "search"
    popular_catalog_path = ""

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        response = await self._request(
            "GET",
            f"{self.base_url}/{self.search_path}",
            params={"s": query.strip(), "page": "1"},
        )
        response.raise_for_status()
        return self._listing(response.text, str(response.url), popular=False)[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"}:
            return []
        if kind == "popular" and self.popular_catalog_path:
            url = f"{self.base_url}/{self.popular_catalog_path.strip('/')}"
            params = {"m_orderby": "views", "page": str(page)}
        else:
            url = self.base_url
            params = {} if kind == "popular" else {"page": str(page)}
        response = await self._request("GET", url, params=params)
        response.raise_for_status()
        return self._listing(
            response.text,
            str(response.url),
            popular=kind == "popular" and not self.popular_catalog_path,
        )

    def _listing(self, html: str, response_url: str, *, popular: bool) -> list[SourceSeries]:
        root = _parse_html(html)
        if popular:
            items = [
                node
                for node in root.descendants()
                if node.has_class("item") and self._has_ancestor_id(node, "slide-top")
            ]
        else:
            items = [node for node in root.descendants() if node.has_class("page-item-detail")]
        result: list[SourceSeries] = []
        for item in items:
            holder = _first(
                item,
                lambda node: node.has_class("info-item" if popular else "item-summary"),
            )
            anchor = _first(
                holder or item,
                lambda node: node.tag == "a" and bool(node.attrs.get("href")),
            )
            if anchor is None:
                continue
            title = anchor.text().strip()
            if title:
                result.append(
                    SourceSeries(
                        source_id=urljoin(response_url, anchor.attrs["href"]),
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
        result: list[SourceChapter] = []
        for item in (
            node
            for node in root.descendants("li")
            if node.has_class("wp-manga-chapter")
        ):
            anchor = _first(
                item,
                lambda node: node.tag == "a" and bool(node.attrs.get("href")),
            )
            if anchor is None:
                continue
            title = anchor.text().strip()
            match = re.search(r"(\d+(?:\.\d+)?)", title)
            result.append(
                SourceChapter(
                    source_id=urljoin(str(response.url), anchor.attrs["href"]),
                    title=title or "Capítulo",
                    series_id=series_id,
                    source_name=self.name,
                    number=float(match.group(1)) if match else None,
                )
            )
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        response = await self._request("GET", chapter_id)
        response.raise_for_status()
        root = _parse_html(response.text)
        urls = [
            url
            for container in (
                node for node in root.descendants() if node.has_class("page-break")
            )
            for image in container.descendants("img")
            if (url := _image_url(image, str(response.url)))
        ]
        return [
            SourcePage(
                source_id=url,
                chapter_id=chapter_id,
                index=index,
                filename=url.rsplit("/", 1)[-1].split("?", 1)[0] or f"{index}.jpg",
                source_name=self.name,
            )
            for index, url in enumerate(dict.fromkeys(urls), 1)
        ]

    @staticmethod
    def _has_ancestor_id(node: object, node_id: str) -> bool:
        parent = getattr(node, "parent", None)
        while parent is not None:
            if parent.attrs.get("id") == node_id:
                return True
            parent = parent.parent
        return False
