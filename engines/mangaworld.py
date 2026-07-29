"""Implementación común de MangaWorld, incluido su reto MWCookie."""

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


class MangaWorldSource(MadaraSource):
    async def _get(self, url: str, **kwargs):
        response = await self._request("GET", url, **kwargs)
        cookie = re.search(r'document\.cookie="(MWCookie[^"]+)', response.text)
        if cookie:
            headers = dict(kwargs.get("headers", {}))
            headers["Cookie"] = cookie.group(1)
            response = await self._request("GET", url, **(kwargs | {"headers": headers}))
        response.raise_for_status()
        return response

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        response = await self._get(
            f"{self.base_url}/archive",
            params={"page": "1", "keyword": query.strip()},
        )
        return self._listing(response.text, str(response.url))[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"}:
            return []
        url = f"{self.base_url}/archive" if kind == "popular" else self.base_url
        params = {"page": str(page)}
        if kind == "popular":
            params["sort"] = "most_read"
        response = await self._get(url, params=params)
        return self._listing(response.text, str(response.url))

    def _listing(self, html: str, response_url: str) -> list[SourceSeries]:
        root = _parse_html(html)
        result: list[SourceSeries] = []
        for item in (node for node in root.descendants() if node.has_class("entry")):
            if not self._has_ancestor_class(item, "comics-grid"):
                continue
            anchor = _first(
                item,
                lambda node: node.tag == "a" and bool(node.attrs.get("href")),
            )
            if anchor is None:
                continue
            title = anchor.attrs.get("title", "").strip() or anchor.text().strip()
            if title:
                result.append(
                    SourceSeries(
                        source_id=urljoin(response_url, anchor.attrs["href"]).rstrip("/"),
                        title=title,
                        source_name=self.name,
                    )
                )
        return result

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        response = await self._get(series_id)
        root = _parse_html(response.text)
        result: list[SourceChapter] = []
        for item in (node for node in root.descendants() if node.has_class("chapter")):
            if not self._has_ancestor_class(item, "chapters-wrapper"):
                continue
            anchor = _first(
                item,
                lambda node: node.tag == "a"
                and node.has_class("chap")
                and bool(node.attrs.get("href")),
            )
            if anchor is None:
                continue
            label = _first(item, lambda node: node.has_class("d-inline-block"))
            title = (label.text() if label else anchor.text()).strip()
            url = urljoin(str(response.url), anchor.attrs["href"])
            url = url.replace("style=pages", "style=list")
            if "style=list" not in url:
                url += "&style=list" if "?" in url else "?style=list"
            match = re.search(r"capitolo\s*(\d+(?:\.\d+)?)", title, re.I)
            result.append(
                SourceChapter(
                    source_id=url,
                    title=title or "Capitolo",
                    series_id=series_id,
                    source_name=self.name,
                    number=float(match.group(1)) if match else None,
                )
            )
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        response = await self._get(chapter_id)
        root = _parse_html(response.text)
        holder = _first(root, lambda node: node.attrs.get("id") == "page")
        urls = [
            url
            for image in (holder.descendants("img") if holder else [])
            if image.has_class("page-image")
            and (url := _image_url(image, str(response.url)))
        ]
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

    @staticmethod
    def _has_ancestor_class(node: object, class_name: str) -> bool:
        parent = getattr(node, "parent", None)
        while parent is not None:
            if parent.has_class(class_name):
                return True
            parent = parent.parent
        return False
