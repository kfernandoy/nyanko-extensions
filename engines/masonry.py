"""Implementación común de galerías Masonry para Nyanko Source v4."""

from urllib.parse import quote, urljoin, urlsplit

try:
    from .base import (
        FuenteBaseSource,
        SourceChapter,
        SourcePage,
        SourceSeries,
        _first,
        _parse_html,
    )
except ImportError:
    pass


class MasonrySource(FuenteBaseSource):
    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        response = await self._request(
            "GET",
            f"{self.base_url}/search/post/{quote(query.strip(), safe='')}/mpage/1/",
        )
        response.raise_for_status()
        return self._listing(response.text, str(response.url))[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind == "popular":
            url = (
                self.base_url
                if page == 1
                else f"{self.base_url}/archive/"
                if page == 2
                else f"{self.base_url}/archive/page/{page - 1}/"
            )
        elif kind == "latest":
            url = f"{self.base_url}/updates/sort/newest/mpage/{page}/"
        else:
            return []
        response = await self._request("GET", url)
        response.raise_for_status()
        return self._listing(response.text, str(response.url))

    def _listing(self, html: str, response_url: str) -> list[SourceSeries]:
        root = _parse_html(html)
        result: list[SourceSeries] = []
        for figure in root.descendants("figure"):
            if not self._has_gallery_ancestor(figure):
                continue
            anchor = _first(figure, lambda node: node.tag == "a" and bool(node.attrs.get("href")))
            if anchor is None or "/video/" in anchor.attrs["href"]:
                continue
            title = anchor.attrs.get("title", "").strip() or anchor.text().strip()
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
        return [
            SourceChapter(
                source_id=series_id,
                title="Gallery",
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
        urls = [
            urljoin(str(response.url), anchor.attrs["href"])
            for anchor in root.descendants("a")
            if anchor.attrs.get("href")
            and urlsplit(urljoin(str(response.url), anchor.attrs["href"])).hostname.startswith("cdn.")
            and self._has_gallery_ancestor(anchor)
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
    def _has_gallery_ancestor(node: object) -> bool:
        parent = getattr(node, "parent", None)
        while parent is not None:
            if parent.has_class("list-gallery") and not parent.has_class("static"):
                return True
            parent = parent.parent
        return False
