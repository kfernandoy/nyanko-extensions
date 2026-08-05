"""Implementación común de sitios MangaReader para Nyanko Source v4."""

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


class MangaReaderSource(MadaraSource):
    search_at_root = False
    search_keyword = "keyword"
    page_parameter = "page"
    chapter_container_id = "en-chapters"
    ajax_kind = "default"
    exclude_manganow_placeholder = False

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        path = "" if self.search_at_root else "/search"
        response = await self._request(
            "GET",
            f"{self.base_url}{path}",
            params={self.search_keyword: query.strip(), self.page_parameter: "1"},
        )
        response.raise_for_status()
        return self._listing(response.text, str(response.url))[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"}:
            return []
        response = await self._request(
            "GET",
            f"{self.base_url}/filter",
            params={
                "sort": "most-viewed" if kind == "popular" else "latest-updated",
                self.page_parameter: str(page),
            },
        )
        response.raise_for_status()
        return self._listing(response.text, str(response.url))

    def _listing(self, html: str, response_url: str) -> list[SourceSeries]:
        root = _parse_html(html)
        result: list[SourceSeries] = []
        for anchor in root.descendants("a"):
            if not anchor.has_class("manga-poster") or not anchor.attrs.get("href"):
                continue
            image = _first(anchor, lambda node: node.tag == "img")
            title = image.attrs.get("alt", "").strip() if image else anchor.text().strip()
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
        holder = _first(root, lambda node: node.attrs.get("id") == self.chapter_container_id)
        items = (
            [node for node in holder.descendants("li") if node.has_class("chapter-item")]
            if holder
            else []
        )
        result: list[SourceChapter] = []
        for item in items:
            anchor = _first(
                item,
                lambda node: node.tag == "a" and bool(node.attrs.get("href")),
            )
            if anchor is None:
                continue
            label = _first(anchor, lambda node: node.has_class("name"))
            title = (label.text() if label else anchor.text()).strip()
            chapter_url = urljoin(str(response.url), anchor.attrs["href"])
            reading_id = item.attrs.get("data-id", "")
            source_id = f"{chapter_url}#{reading_id}" if reading_id else chapter_url
            match = re.search(r"(\d+(?:\.\d+)?)", title)
            result.append(
                SourceChapter(
                    source_id=source_id,
                    title=title or "Capítulo",
                    series_id=series_id,
                    source_name=self.name,
                    number=float(match.group(1)) if match else None,
                )
            )
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        source_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        chapter_url, separator, reading_id = source_id.rpartition("#")
        if not separator:
            chapter_url = source_id
            response = await self._request("GET", chapter_url)
            response.raise_for_status()
            root = _parse_html(response.text)
            holder = _first(root, lambda node: bool(node.attrs.get("data-reading-id")))
            reading_id = holder.attrs.get("data-reading-id", "") if holder else ""
        if not reading_id:
            return []
        if self.ajax_kind == "json":
            ajax_url = f"{self.base_url}/json/chapter"
            params = {"mode": "vertical", "id": reading_id}
        else:
            ajax_url = f"{self.base_url}/ajax/image/list/{reading_id}"
            params = {"mode": "vertical"}
        response = await self._request(
            "GET",
            ajax_url,
            params=params,
            headers={"Referer": chapter_url, "X-Requested-With": "XMLHttpRequest"},
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except (AttributeError, json.JSONDecodeError):
            payload = json.loads(response.text)
        root = _parse_html(str(payload.get("html", "")))
        reader = _first(root, lambda node: node.has_class("container-reader-chapter"))
        urls: list[str] = []
        for image in reader.descendants("img") if reader else []:
            card = image.parent
            if (
                self.exclude_manganow_placeholder
                and card is not None
                and card.has_class("iv-card")
                and card.attrs.get("data-url", "").endswith("manganow.jpg")
            ):
                continue
            url = _image_url(image, chapter_url)
            if url:
                urls.append(url)
        return [
            SourcePage(
                source_id=url,
                chapter_id=source_id,
                index=index,
                filename=url.rsplit("/", 1)[-1].split("?", 1)[0] or f"{index}.jpg",
                source_name=self.name,
            )
            for index, url in enumerate(dict.fromkeys(urls), 1)
        ]
