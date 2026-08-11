"""Implementación HTML común de ZManga."""

from urllib.parse import urljoin

try:
    from .base import FuenteBaseSource, SourceChapter, SourcePage, SourceSeries, _first, _image_url, _parse_html
except ImportError:
    pass


class ZMangaSource(FuenteBaseSource):
    def _series(self, html: str) -> list[SourceSeries]:
        root = _parse_html(html)
        result: list[SourceSeries] = []
        for item in (node for node in root.descendants("div") if node.has_class("flexbox2-item")):
            link = _first(item, lambda node: node.tag == "a" and node.attrs.get("href"))
            title = _first(item, lambda node: node.tag == "span" and node.text())
            if link and title:
                result.append(
                    SourceSeries(
                        source_id=urljoin(self.base_url, link.attrs["href"]),
                        title=title.text(),
                        source_name=self.name,
                    )
                )
        return result

    async def _catalog(self, order: str, page: int) -> list[SourceSeries]:
        suffix = f"page/{page}/" if page > 1 else ""
        response = await self._request(
            "GET",
            f"{self.base_url}/advanced-search/{suffix}",
            params={"order": order},
        )
        response.raise_for_status()
        return self._series(response.text)

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        response = await self._request(
            "GET",
            f"{self.base_url}/advanced-search/",
            params={"title": query.strip()},
        )
        response.raise_for_status()
        return self._series(response.text)[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"}:
            return []
        return await self._catalog("popular" if kind == "popular" else "update", page)

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        response = await self._request("GET", series_id)
        response.raise_for_status()
        root = _parse_html(response.text)
        result: list[SourceChapter] = []
        for block in (node for node in root.descendants("div") if node.has_class("flexch-infoz")):
            link = _first(block, lambda node: node.tag == "a" and node.attrs.get("href"))
            if not link:
                continue
            name = next(
                (node.text() for node in link.descendants("span") if not node.has_class("date") and node.text()),
                link.text(),
            )
            result.append(
                SourceChapter(
                    source_id=urljoin(series_id, link.attrs["href"]),
                    title=name,
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
        reader = _first(root, lambda node: node.tag == "div" and node.has_class("reader-area"))
        if reader is None:
            return []
        urls = [_image_url(node, chapter_id) for node in reader.descendants("img")]
        return [
            SourcePage(
                source_id=url,
                chapter_id=chapter_id,
                index=index,
                filename=url.rsplit("/", 1)[-1].split("?", 1)[0],
                source_name=self.name,
            )
            for index, url in enumerate(urls, 1)
            if url
        ]
