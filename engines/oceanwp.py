"""Implementación común de sitios OceanWP para Nyanko Source v3."""

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


class OceanWPSource(MadaraSource):
    supports_latest = False

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        response = await self._request("GET", self.base_url, params={"s": query.strip()})
        response.raise_for_status()
        return self._listing(response.text, str(response.url))[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind != "popular":
            return []
        url = self.base_url if page == 1 else f"{self.base_url}/page/{page}/"
        response = await self._request("GET", url)
        response.raise_for_status()
        return self._listing(response.text, str(response.url))

    def _listing(self, html: str, response_url: str) -> list[SourceSeries]:
        root = _parse_html(html)
        result: list[SourceSeries] = []
        for article in root.descendants("article"):
            heading = _first(
                article,
                lambda node: node.tag == "h2"
                and (node.has_class("blog-entry-title") or node.has_class("search-entry-title")),
            )
            anchor = _first(
                heading or article,
                lambda node: node.tag == "a" and bool(node.attrs.get("href")),
            )
            if anchor is not None and anchor.text().strip():
                result.append(
                    SourceSeries(
                        source_id=urljoin(response_url, anchor.attrs["href"]),
                        title=anchor.text().strip(),
                        source_name=self.name,
                    )
                )
        return result

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        return [
            SourceChapter(
                source_id=series_id,
                title="Chapter 1",
                series_id=series_id,
                source_name=self.name,
                number=1,
            )
        ]

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        response = await self._request("GET", chapter_id)
        response.raise_for_status()
        root = _parse_html(response.text)
        content = _first(root, lambda node: node.has_class("entry-content"))
        urls = [
            url
            for image in (content.descendants("img") if content else [])
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
            for index, url in enumerate(urls, 1)
        ]
