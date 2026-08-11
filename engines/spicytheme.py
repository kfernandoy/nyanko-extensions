"""Implementación común de SpicyTheme."""

import json

try:
    from .base import FuenteBaseSource, SourceChapter, SourcePage, SourceSeries
except ImportError:
    pass


class SpicyThemeSource(FuenteBaseSource):
    api_base_url = ""
    requests_per_minute = 120

    @property
    def api_url(self) -> str:
        return self.api_base_url or self.base_url.replace("https://", "https://api.", 1)

    def _series(self, payload) -> list[SourceSeries]:
        rows = payload.get("data", []) if isinstance(payload, dict) else payload
        return [
            SourceSeries(
                source_id=f"{self.base_url}/comic/{row['slug']}",
                title=str(row["name"]).strip(),
                source_name=self.name,
            )
            for row in rows
            if row.get("slug") and row.get("name")
        ]

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        response = await self._request(
            "GET",
            f"{self.api_url}/home/buscar",
            params={"query": query.strip()},
        )
        response.raise_for_status()
        return self._series(response.json())[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"}:
            return []
        response = await self._request(
            "GET",
            f"{self.api_url}/filtrar",
            params={
                "page": str(page),
                "limit": "12",
                "orderBy": "users_count" if kind == "popular" else "created_at",
                "sort": "desc",
                "gendersId": "",
                "origin": "",
                "state": "",
                "loading": "true",
            },
        )
        response.raise_for_status()
        return self._series(response.json())

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        slug = series_id.rstrip("/").rsplit("/", 1)[-1]
        response = await self._request("GET", f"{self.api_url}/serie/{slug}")
        response.raise_for_status()
        manga = response.json().get("serie", {})
        return [
            SourceChapter(
                source_id=f"{self.base_url}/comic/{slug}/{row['slug']}",
                title=f"Capítulo {row['num']:g}" if isinstance(row.get("num"), (int, float)) else "Capítulo",
                series_id=series_id,
                source_name=self.name,
                number=float(row["num"]) if isinstance(row.get("num"), (int, float)) else None,
                uploaded_at=row.get("createdAt"),
            )
            for row in manga.get("chapters") or []
            if row.get("slug")
        ]

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        path = chapter_id.split("/comic/", 1)[-1]
        response = await self._request("GET", f"{self.api_url}/serie/{path}/")
        response.raise_for_status()
        pages = response.json().get("pageches", {})
        if isinstance(pages, list):
            pages = pages[0] if pages else {}
        raw = pages.get("urlImg", "[]")
        urls = json.loads(raw) if isinstance(raw, str) else raw
        return [
            SourcePage(
                source_id=url,
                chapter_id=chapter_id,
                index=index,
                filename=url.rsplit("/", 1)[-1].split("?", 1)[0] or f"{index}.jpg",
                source_name=self.name,
            )
            for index, url in enumerate(urls, 1)
            if url
        ]
