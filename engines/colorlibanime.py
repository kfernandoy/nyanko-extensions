"""Implementación común de sitios ColorlibAnime para Nyanko Source v4."""

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


class ColorlibAnimeSource(FuenteBaseSource):
    requests_per_minute = 180

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        return (await self._catalog(query.strip(), "view", 1))[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"}:
            return []
        return await self._catalog("", "view" if kind == "popular" else "updated", page)

    async def _catalog(self, query: str, sort: str, page: int) -> list[SourceSeries]:
        response = await self._request(
            "GET",
            f"{self.base_url}/manga",
            params={"page": str(page), "sort": sort, "search": query},
        )
        response.raise_for_status()
        root = _parse_html(response.text)
        result: list[SourceSeries] = []
        for item in (
            node for node in root.descendants() if node.has_class("product__item")
        ):
            anchor = _first(
                item,
                lambda node: node.tag == "a"
                and node.has_class("img-link")
                and bool(node.attrs.get("href")),
            )
            heading = _first(item, lambda node: node.tag == "h5")
            title = heading.text().strip() if heading else ""
            if anchor is not None and title:
                result.append(
                    SourceSeries(
                        source_id=urljoin(str(response.url), anchor.attrs["href"]),
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
        holder = _first(root, lambda node: node.has_class("anime__details__episodes"))
        result: list[SourceChapter] = []
        for anchor in holder.descendants("a") if holder else []:
            href = anchor.attrs.get("href", "")
            if not href:
                continue
            title = anchor.text().strip()
            match = re.search(r"(\d+(?:\.\d+)?)", title)
            result.append(
                SourceChapter(
                    source_id=urljoin(str(response.url), href),
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
        readers = [node for node in root.descendants() if node.has_class("read-img")]
        urls = [
            url
            for reader in readers
            for image in reader.descendants("img")
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
