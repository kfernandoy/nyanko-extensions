"""Implementación común de MangaBox sin la fusión opcional de imágenes."""

import re
import unicodedata
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


def _search_slug(query: str) -> str:
    value = unicodedata.normalize("NFKD", query.casefold().replace("đ", "d"))
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]", "_", value)).strip("_")


class MangaBoxSource(FuenteBaseSource):
    chapter_profile = "regular"

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        response = await self._request(
            "GET",
            f"{self.base_url}/search/story/{_search_slug(query)}",
            params={"page": "1"},
        )
        response.raise_for_status()
        return self._listing(response.text, str(response.url))[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"}:
            return []
        path = "manga-list/hot-manga" if kind == "popular" else "manga-list/latest-manga"
        response = await self._request("GET", f"{self.base_url}/{path}", params={"page": str(page)})
        response.raise_for_status()
        return self._listing(response.text, str(response.url))

    def _listing(self, html: str, response_url: str) -> list[SourceSeries]:
        root = _parse_html(html)
        result: list[SourceSeries] = []
        classes = {"list-truyen-item-wrap", "list-comic-item-wrap", "story_item"}
        for card in (node for node in root.descendants() if any(node.has_class(name) for name in classes)):
            heading = _first(card, lambda node: node.tag == "h3")
            anchor = _first(
                heading or card,
                lambda node: node.tag == "a" and bool(node.attrs.get("href")),
            )
            title = anchor.text().strip() if anchor else ""
            if anchor is not None and title:
                result.append(SourceSeries(urljoin(response_url, anchor.attrs["href"]), title, self.name))
        return result

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        slug = series_id.rstrip("/").rsplit("/", 1)[-1]
        limit = -1 if self.chapter_profile == "kakalot" else 1000
        offset = 0
        rows: list[dict] = []
        while True:
            response = await self._request(
                "GET",
                f"{self.base_url}/api/manga/{slug}/chapters",
                params={"limit": str(limit), "offset": str(offset)},
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("success") is False:
                return []
            data = payload.get("data") or {}
            rows.extend(data.get("chapters", []))
            if not data.get("pagination", {}).get("has_more"):
                break
            offset += limit
        result: list[SourceChapter] = []
        for row in rows:
            chapter_slug = row.get("chapter_slug")
            if not chapter_slug:
                continue
            number = row.get("chapter_num")
            result.append(
                SourceChapter(
                    source_id=f"{self.base_url}/manga/{slug}/{chapter_slug}",
                    title=str(row.get("chapter_name", "")).strip() or "Chapter",
                    series_id=series_id,
                    source_name=self.name,
                    number=float(number) if isinstance(number, (int, float)) else None,
                    uploaded_at=row.get("updated_at"),
                )
            )
        return result

    @staticmethod
    def _array(script: str, name: str) -> list[str]:
        match = re.search(rf"{name}\s*=\s*\[([^]]+)]", script)
        return [
            item.strip().strip('"').replace("\\/", "/").rstrip("/")
            for item in match.group(1).split(",")
        ] if match else []

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        response = await self._request("GET", chapter_id)
        response.raise_for_status()
        cdns = self._array(response.text, "cdns") + self._array(response.text, "backupImage")
        paths = self._array(response.text, "chapterImages")
        if cdns and paths:
            urls = [urljoin(f"{cdns[0].rstrip('/')}/", path.lstrip("/")) for path in paths]
        else:
            root = _parse_html(response.text)
            holder = _first(root, lambda node: node.has_class("container-chapter-reader"))
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
