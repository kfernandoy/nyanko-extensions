"""Implementación común de sitios Keyoapp para Nyanko Source v3."""

import json
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


class KeyoappSource(MadaraSource):
    search_profile = "default"
    popular_profile = "default"
    pages_profile = "default"

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        path = "/search" if self.search_profile in {"artlapsa", "rithar"} else "/series/"
        response = await self._request(
            "GET",
            f"{self.base_url}{path}",
            params={
                ("title" if self.search_profile in {"artlapsa", "rithar"} else "q"): query.strip()
            },
        )
        response.raise_for_status()
        result = self._listing(response.text, str(response.url), search=True)
        if self.search_profile == "timeless" and query.strip():
            result = [item for item in result if query.strip().lower() in item.title.lower()]
        return result[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"} or page != 1:
            return []
        if kind == "popular" and self.popular_profile == "search":
            return await self.search("")
        response = await self._request(
            "GET",
            self.base_url if kind == "popular" else f"{self.base_url}/latest/",
        )
        response.raise_for_status()
        return self._listing(response.text, str(response.url), search=False)

    def _listing(self, html: str, response_url: str, *, search: bool) -> list[SourceSeries]:
        root = _parse_html(html)
        result: list[SourceSeries] = []
        seen: set[str] = set()
        for node in root.descendants():
            candidate = (
                search
                and node.tag == "button"
                and self._has_ancestor_id(node, "searched_series_page")
            ) or (
                search
                and node.has_class("group")
            ) or (
                search
                and self.search_profile in {"artlapsa", "rithar"}
                and (
                    "serie" in node.attrs.get("wire:key", "")
                    or node.tag == "button"
                    and bool(node.attrs.get("tags"))
                )
            ) or (
                not search
                and (
                    (
                        node.has_class("group")
                        and (node.has_class("grid") or self._has_ancestor_class(node, "grid"))
                    )
                    or node.has_class("splide__slide")
                    or self.popular_profile == "all_groups"
                    and node.has_class("group")
                )
            )
            if not candidate:
                continue
            anchor = _first(node, lambda item: item.tag == "a" and bool(item.attrs.get("href")))
            if anchor is None and node.tag == "a":
                anchor = node
            if anchor is None:
                continue
            source_id = urljoin(response_url, anchor.attrs["href"])
            title = anchor.attrs.get("title", "").strip() or node.attrs.get("title", "").strip()
            if source_id in seen or not title:
                continue
            seen.add(source_id)
            result.append(SourceSeries(source_id=source_id, title=title, source_name=self.name))
        return result

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        response = await self._request("GET", series_id)
        response.raise_for_status()
        root = _parse_html(response.text)
        chapters = _first(root, lambda node: node.attrs.get("id") == "chapters")
        if chapters is None:
            return []
        result: list[SourceChapter] = []
        for anchor in chapters.descendants("a"):
            href = anchor.attrs.get("href", "")
            label = _first(anchor, lambda node: node.has_class("text-sm"))
            if not href or label is None or "Upcoming" in label.text():
                continue
            paid = _first(
                anchor,
                lambda node: node.tag == "img" and "Coin" in node.attrs.get("alt", ""),
            )
            if paid is not None:
                continue
            title = label.text().strip()
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
        if self.pages_profile == "ld_json":
            root = _parse_html(response.text)
            script = next(
                (
                    node
                    for node in root.descendants("script")
                    if node.attrs.get("type") == "application/ld+json" and node.text().strip()
                ),
                None,
            )
            data = json.loads(script.text()) if script else {}
            chapter_slug = str(data.get("url", "")).rstrip("/").rsplit("/", 1)[-1]
            series_slug = str((data.get("isPartOf") or {}).get("url", "")).rstrip("/").rsplit("/", 1)[-1]
            count = int(data.get("numberOfPages") or 0)
            urls = [
                f"{self.base_url}/storage/series/webtoon/{series_slug}/chapters/{chapter_slug}/{page:03}.jpg"
                for page in range(1, count + 1)
            ]
            return self._source_pages(urls, chapter_id)
        root = _parse_html(response.text)
        pages = _first(root, lambda node: node.attrs.get("id") == "pages")
        images = pages.descendants("img") if pages else []
        cdn_match = re.search(r"realUrl\s*=\s*`[^`]+//([^/]+)", response.text)
        cdn = re.sub(r"\$\{[^}]*}", "", cdn_match.group(1)) if cdn_match else ""
        urls: list[str] = []
        for image in images:
            uid = image.attrs.get("uid", "")
            if uid and cdn:
                urls.append(f"https://{cdn}/uploads/{uid}")
            else:
                url = _image_url(image, str(response.url))
                if url:
                    urls.append(url)
        return self._source_pages(urls, chapter_id)

    def _source_pages(self, urls: list[str], chapter_id: str) -> list[SourcePage]:
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
    def _has_ancestor_class(node: object, class_name: str) -> bool:
        parent = getattr(node, "parent", None)
        while parent is not None:
            if parent.has_class(class_name):
                return True
            parent = parent.parent
        return False

    @staticmethod
    def _has_ancestor_id(node: object, node_id: str) -> bool:
        parent = getattr(node, "parent", None)
        while parent is not None:
            if parent.attrs.get("id") == node_id:
                return True
            parent = parent.parent
        return False
