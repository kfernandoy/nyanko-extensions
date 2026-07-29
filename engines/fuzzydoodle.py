"""Implementación común de FuzzyDoodle."""

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


class FuzzyDoodleSource(MadaraSource):
    latest_profile = "regular"

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        response = await self._request("GET", f"{self.base_url}/manga", params={"title": query.strip()})
        response.raise_for_status()
        return self._listing(response.text, str(response.url))[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind == "popular":
            url = f"{self.base_url}/manga?page={page}"
        elif kind == "latest" and self.latest_profile == "home":
            url = f"{self.base_url}/?page={page}"
        elif kind == "latest":
            path = "latest-manga" if self.latest_profile == "manga" else "latest"
            url = f"{self.base_url}/{path}?page={page}"
        else:
            return []
        response = await self._request("GET", url)
        response.raise_for_status()
        return self._listing(response.text, str(response.url), latest_home=kind == "latest" and self.latest_profile == "home")

    def _listing(self, html: str, response_url: str, *, latest_home: bool = False) -> list[SourceSeries]:
        root = _parse_html(html)
        scope = root
        if latest_home:
            scope = _first(
                root,
                lambda node: node.tag == "section"
                and any(
                    phrase in node.text()
                    for phrase in ("Recent Chapters", "Chapitres récents")
                ),
            ) or root
        result: list[SourceSeries] = []
        for card in (
            node
            for node in scope.descendants()
            if node.attrs.get("id") == "card-real"
        ):
            anchor = _first(card, lambda node: node.tag == "a" and bool(node.attrs.get("href")))
            heading = _first(card, lambda node: node.tag == "h2" and node.has_class("text-sm"))
            title = heading.text().strip() if heading else ""
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
        response = await self._request("GET", series_id)
        result: list[SourceChapter] = []
        while True:
            response.raise_for_status()
            root = _parse_html(response.text)
            holder = _first(root, lambda node: node.attrs.get("id") == "chapters-list")
            for anchor in holder.descendants("a") if holder else []:
                if not anchor.attrs.get("href"):
                    continue
                title_node = _first(
                    anchor,
                    lambda node: node.attrs.get("id") == "item-title" or node.tag == "span",
                )
                result.append(
                    SourceChapter(
                        source_id=urljoin(str(response.url), anchor.attrs["href"]),
                        title=title_node.text().strip() if title_node else anchor.text().strip() or "Chapter",
                        series_id=series_id,
                        source_name=self.name,
                    )
                )
            pagination = _first(root, lambda node: node.tag == "ul" and node.has_class("pagination"))
            items = [
                node
                for node in (pagination.children if pagination else [])
                if not isinstance(node, str) and node.tag == "li"
            ]
            next_link = (
                _first(items[-1], lambda node: node.tag == "a")
                if items and not items[-1].has_class("pagination-disabled")
                else None
            )
            if next_link is None or not next_link.attrs.get("href"):
                break
            response = await self._request("GET", urljoin(str(response.url), next_link.attrs["href"]))
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        response = await self._request("GET", chapter_id)
        response.raise_for_status()
        root = _parse_html(response.text)
        holder = _first(root, lambda node: node.attrs.get("id") == "chapter-container")
        urls = [
            url
            for image in (holder.descendants("img") if holder else [])
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
