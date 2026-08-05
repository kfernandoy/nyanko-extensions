"""Implementación común de sitios Manga18 para Nyanko Source v4."""

import base64
import re
from urllib.parse import urljoin

try:
    from .madara import (
        MadaraSource,
        SourceChapter,
        SourcePage,
        SourceSeries,
        _first,
        _parse_html,
    )
except ImportError:
    pass


class Manga18Source(MadaraSource):
    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        response = await self._request(
            "GET",
            f"{self.base_url}/list-manga/1",
            params={"search": query.strip()},
        )
        response.raise_for_status()
        return self._listing(response.text, str(response.url))[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"}:
            return []
        params = {"order_by": "views"} if kind == "popular" else {}
        response = await self._request(
            "GET",
            f"{self.base_url}/list-manga/{page}",
            params=params,
        )
        response.raise_for_status()
        return self._listing(response.text, str(response.url))

    def _listing(self, html: str, response_url: str) -> list[SourceSeries]:
        root = _parse_html(html)
        result: list[SourceSeries] = []
        for item in (node for node in root.descendants() if node.has_class("story_item")):
            name = _first(item, lambda node: node.has_class("mg_name"))
            anchor = _first(
                name or item,
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
        for box in (node for node in root.descendants() if node.has_class("chapter_box")):
            for item in (node for node in box.descendants() if node.has_class("item")):
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
        match = re.search(r"slides_p_path.*?\[(.*?)\]", response.text, re.S)
        if match is None:
            return []
        urls: list[str] = []
        for encoded in re.findall(r'"([^"]+)"', match.group(1)):
            try:
                url = base64.b64decode(encoded).decode()
            except (ValueError, UnicodeDecodeError):
                continue
            urls.append(urljoin(f"{self.base_url}/", url))
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
