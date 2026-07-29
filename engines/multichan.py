"""Implementación común de MangaChan, YaoiChan y HenChan."""

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


class MultiChanSource(MadaraSource):
    profile = "regular"
    requests_per_minute = 120

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        response = await self._request(
            "GET",
            self.base_url,
            params={
                "do": "search",
                "subaction": "search",
                "story": query.strip(),
                "search_start": "1",
            },
        )
        response.raise_for_status()
        return self._listing(response.text, str(response.url))[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"}:
            return []
        if kind == "popular":
            path = "mostfavorites"
        else:
            path = "manga/newest" if self.profile == "henchan" else "manga/new"
        response = await self._request(
            "GET",
            f"{self.base_url}/{path}",
            params={"offset": str(20 * max(page - 1, 0))},
        )
        response.raise_for_status()
        return self._listing(response.text, str(response.url))

    def _listing(self, html: str, response_url: str) -> list[SourceSeries]:
        root = _parse_html(html)
        result: list[SourceSeries] = []
        for item in (node for node in root.descendants() if node.has_class("content_row")):
            if self.profile == "henchan" and "Тип" in item.text():
                continue
            heading = _first(item, lambda node: node.tag == "h2")
            anchor = _first(
                heading or item,
                lambda node: node.tag == "a" and bool(node.attrs.get("href")),
            )
            title = item.attrs.get("title", "").strip()
            if anchor is not None and title:
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
        if self.profile != "henchan":
            response = await self._request("GET", series_id)
            response.raise_for_status()
            return self._regular_chapters(response.text, str(response.url), series_id)

        related_url = series_id.replace("/manga/", "/related/")
        response = await self._request("GET", related_url)
        if getattr(response, "status_code", 200) == 404:
            response = await self._request("GET", series_id)
        response.raise_for_status()
        result = self._hen_chapters(response.text, str(response.url), series_id)
        next_url = self._next_url(response.text, str(response.url))
        while next_url:
            response = await self._request("GET", next_url)
            response.raise_for_status()
            result.extend(self._hen_chapters(response.text, str(response.url), series_id))
            next_url = self._next_url(response.text, str(response.url))
        return list(reversed(result))

    def _regular_chapters(
        self,
        html: str,
        response_url: str,
        series_id: str,
    ) -> list[SourceChapter]:
        root = _parse_html(html)
        table = _first(root, lambda node: node.has_class("table_cha"))
        items = table.descendants("tr") if table else []
        return [
            chapter
            for item in items
            if (chapter := self._chapter(item, response_url, series_id)) is not None
        ]

    def _hen_chapters(
        self,
        html: str,
        response_url: str,
        series_id: str,
    ) -> list[SourceChapter]:
        if "/manga/" in response_url:
            root = _parse_html(html)
            title_anchor = _first(root, lambda node: node.has_class("title_top_a"))
            return [
                SourceChapter(
                    source_id=response_url,
                    title=title_anchor.text().strip() if title_anchor else "Chapter",
                    series_id=series_id,
                    source_name=self.name,
                    number=1,
                )
            ]
        root = _parse_html(html)
        return [
            chapter
            for item in (node for node in root.descendants() if node.has_class("related"))
            if (chapter := self._chapter(item, response_url, series_id, heading=True)) is not None
        ]

    def _chapter(
        self,
        item,
        response_url: str,
        series_id: str,
        *,
        heading: bool = False,
    ) -> SourceChapter | None:
        holder = _first(item, lambda node: node.tag == "h2") if heading else item
        anchor = _first(
            holder or item,
            lambda node: node.tag == "a" and bool(node.attrs.get("href")),
        )
        if anchor is None:
            return None
        title = anchor.attrs.get("title", "").strip() or anchor.text().strip()
        match = re.search(r"(?:глава|часть)\s*(\d+(?:\.\d+)?)", title, re.I)
        return SourceChapter(
            source_id=urljoin(response_url, anchor.attrs["href"]),
            title=title or "Глава",
            series_id=series_id,
            source_name=self.name,
            number=float(match.group(1)) if match else None,
        )

    def _next_url(self, html: str, response_url: str) -> str:
        root = _parse_html(html)
        pagination = _first(root, lambda node: node.attrs.get("id") == "pagination_related")
        if pagination:
            anchor = _first(
                pagination,
                lambda node: node.tag == "a"
                and "Вперед" in node.text()
                and bool(node.attrs.get("href")),
            )
            if anchor:
                return urljoin(response_url, anchor.attrs["href"])
        return ""

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        url = (
            chapter_id.replace("/manga/", "/online/")
            if self.profile == "henchan" and "/manga/" in chapter_id
            else chapter_id
        )
        response = await self._request("GET", url)
        response.raise_for_status()
        match = re.search(r'fullimg"\s*:\s*\[(.*?)]', response.text, re.S)
        if match is None:
            return []
        urls = re.findall(r"""['"]([^'"]+)['"]""", match.group(1))
        return [
            SourcePage(
                source_id=page_url,
                chapter_id=chapter_id,
                index=index,
                filename=page_url.rsplit("/", 1)[-1].split("?", 1)[0] or f"{index}.jpg",
                source_name=self.name,
            )
            for index, page_url in enumerate(urls, 1)
        ]
