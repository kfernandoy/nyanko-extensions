"""Implementación común de StalkerCMS."""

from urllib.parse import urljoin

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


class StalkerCmsSource(FuenteBaseSource):
    requests_per_minute = 120

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        response = await self._request(
            "GET",
            f"{self.base_url}/search/live-search/",
            params={"q": query.strip()},
        )
        response.raise_for_status()
        return [
            SourceSeries(
                source_id=urljoin(self.base_url, row["url"]),
                title=str(row["title"]).strip(),
                source_name=self.name,
            )
            for row in response.json().get("results", [])
            if row.get("url") and row.get("title")
        ][:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind == "popular":
            response = await self._request(
                "GET",
                f"{self.base_url}/manga/todos/",
                params={"page": str(page)},
            )
            response.raise_for_status()
            return self._listing(response.text, str(response.url))
        if kind != "latest":
            return []
        if page == 1:
            response = await self._request("GET", self.base_url)
            response.raise_for_status()
            return self._listing(response.text, str(response.url))
        response = await self._request(
            "GET",
            f"{self.base_url}/manga/ajax/load-more-releases/",
            params={"page": str(page)},
        )
        response.raise_for_status()
        return self._listing(response.json().get("html", ""), self.base_url)

    def _listing(self, html: str, response_url: str) -> list[SourceSeries]:
        root = _parse_html(html)
        cards = [
            node
            for node in root.descendants()
            if node.has_class("comic-card-link") or node.has_class("manga-card-simple")
        ]
        result: list[SourceSeries] = []
        for card in cards:
            anchor = card if card.tag == "a" else _first(
                card, lambda node: node.tag == "a" and bool(node.attrs.get("href"))
            )
            heading = _first(card, lambda node: node.tag == "h3")
            title = heading.text().strip() if heading else ""
            if anchor is not None and title:
                result.append(SourceSeries(urljoin(response_url, anchor.attrs["href"]), title, self.name))
        return result

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        result: list[SourceChapter] = []
        page = 1
        while True:
            response = await self._request("GET", series_id, params={"page": str(page)})
            response.raise_for_status()
            root = _parse_html(response.text)
            for anchor in (
                node
                for node in root.descendants("a")
                if node.has_class("chapter-link") and node.attrs.get("href")
            ):
                name = _first(anchor, lambda node: node.has_class("chapter-number"))
                result.append(
                    SourceChapter(
                        source_id=urljoin(str(response.url), anchor.attrs["href"]),
                        title=name.text().strip() if name else anchor.text().strip() or "Capítulo",
                        series_id=series_id,
                        source_name=self.name,
                    )
                )
            next_link = _first(
                root,
                lambda node: node.tag == "a"
                and node.has_class("page-link")
                and node.attrs.get("aria-label") == "Próxima"
                and not node.has_class("disabled"),
            )
            if next_link is None:
                break
            page += 1
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        response = await self._request("GET", chapter_id)
        response.raise_for_status()
        root = _parse_html(response.text)
        urls = [
            urljoin(str(response.url), node.attrs["data-src-url"])
            for node in root.descendants()
            if node.has_class("chapter-image-canvas") and node.attrs.get("data-src-url")
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
