"""Implementación API común de MCCMS."""

from html import unescape
from urllib.parse import urljoin

try:
    from .base import FuenteBaseSource, SourceChapter, SourcePage, SourceSeries, _parse_html
except ImportError:
    pass


class MCCMSSource(FuenteBaseSource):
    requests_per_minute = 120

    async def _json(self, endpoint: str, params: dict) -> list[dict]:
        response = await self._request("GET", f"{self.base_url}{endpoint}", params=params)
        response.raise_for_status()
        data = response.json()
        return data.get("data", []) if isinstance(data, dict) else []

    def _series(self, rows: list[dict]) -> list[SourceSeries]:
        return [
            SourceSeries(
                source_id=f"{urljoin(self.base_url, row['url'].removeprefix('/index.php'))}#{row['id']}",
                title=unescape(row["name"]),
                source_name=self.name,
            )
            for row in rows
            if row.get("id") and row.get("name") and row.get("url")
        ]

    async def _catalog(self, page: int, order: str = "", key: str = "") -> list[SourceSeries]:
        params = {"page": max(page, 1), "size": 30}
        if order:
            params["order"] = order
        if key:
            params["key"] = key
        return self._series(await self._json("/api/data/comic", params))

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        return (await self._catalog(1, key=query.strip()))[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"}:
            return []
        return await self._catalog(page, "hits" if kind == "popular" else "addtime")

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        _, marker, manga_id = series_id.rpartition("#")
        if not marker:
            return []
        rows = await self._json("/api/comic/chapter", {"mid": manga_id})
        return [
            SourceChapter(
                source_id=urljoin(self.base_url, row["link"].removeprefix("/index.php")),
                title=unescape(row["name"]),
                series_id=series_id,
                source_name=self.name,
            )
            for row in reversed(rows)
            if row.get("link") and row.get("name")
        ]

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        response = await self._request(
            "GET",
            chapter_id,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        )
        response.raise_for_status()
        root = _parse_html(response.text)
        images = [
            node.attrs.get("data-original") or node.attrs.get("src")
            for node in root.descendants("img")
            if node.attrs.get("data-original")
        ]
        return [
            SourcePage(
                source_id=urljoin(chapter_id, url),
                chapter_id=chapter_id,
                index=index,
                filename=url.rsplit("/", 1)[-1].split("?", 1)[0],
                source_name=self.name,
            )
            for index, url in enumerate(images, 1)
        ]
