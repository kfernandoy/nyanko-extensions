"""Implementación común de los catálogos monográficos MangaCatalog."""

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


class MangaCatalogSource(MadaraSource):
    source_list: tuple[tuple[str, str], ...] = ()
    supports_latest = False

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        query = query.casefold().strip()
        return [
            series
            for series in self._catalog()
            if query in series.title.casefold()
        ][:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        return self._catalog() if kind == "popular" and page == 1 else []

    def _catalog(self) -> list[SourceSeries]:
        return [
            SourceSeries(source_id=url, title=title, source_name=self.name)
            for title, url in self.source_list
        ]

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        response = await self._request("GET", series_id)
        response.raise_for_status()
        root = _parse_html(response.text)
        grids = [
            node
            for node in root.descendants()
            if node.has_class("grid") and self._has_ancestor_class(node, "bg-bg-secondary")
        ]
        result: list[SourceChapter] = []
        seen: set[str] = set()
        for grid in grids:
            for anchor in grid.descendants("a"):
                href = anchor.attrs.get("href", "")
                if not href:
                    continue
                chapter_url = urljoin(str(response.url), href)
                if chapter_url in seen:
                    continue
                seen.add(chapter_url)
                container = anchor.parent or anchor
                extra = _first(container, lambda node: node.has_class("text-xs"))
                title = anchor.text().strip()
                if extra is not None and extra.text().strip():
                    title = f"{title} - {extra.text().strip()}"
                result.append(
                    SourceChapter(
                        source_id=chapter_url,
                        title=title or "Capítulo",
                        series_id=series_id,
                        source_name=self.name,
                    )
                )
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        response = await self._request("GET", chapter_id)
        response.raise_for_status()
        root = _parse_html(response.text)
        urls = list(
            dict.fromkeys(
                url
                for image in root.descendants("img")
                if image.attrs.get("data-src")
                and (url := _image_url(image, str(response.url)))
            )
        )
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
