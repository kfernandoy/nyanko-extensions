"""Implementación común de Fansubs.cat."""

try:
    from .base import FuenteBaseSource, SourceChapter, SourcePage, SourceSeries
except ImportError:
    pass


class FansubsCatSource(FuenteBaseSource):
    @property
    def api_url(self) -> str:
        return self.base_url.replace("https://manga.", "https://api.", 1)

    def _series(self, payload: dict) -> list[SourceSeries]:
        return [
            SourceSeries(
                source_id=f"{self.base_url}/{row['slug']}",
                title=str(row["name"]).strip(),
                source_name=self.name,
            )
            for row in payload.get("result", [])
            if row.get("slug") and row.get("name")
        ]

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        response = await self._request(
            "GET",
            f"{self.api_url}/manga/search/1",
            params={"query": query.strip(), "type": "all"},
        )
        response.raise_for_status()
        return self._series(response.json())[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"}:
            return []
        path = "popular" if kind == "popular" else "recent"
        response = await self._request("GET", f"{self.api_url}/manga/{path}/{page}")
        response.raise_for_status()
        return self._series(response.json())

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        slug = series_id.rstrip("/").rsplit("/", 1)[-1]
        response = await self._request("GET", f"{self.api_url}/manga/chapters/{slug}")
        response.raise_for_status()
        return [
            SourceChapter(
                source_id=f"{self.api_url}/manga/pages/{row['id']}",
                title=str(row.get("title", "")).strip() or "Chapter",
                series_id=series_id,
                source_name=self.name,
                number=float(row["number"]) if isinstance(row.get("number"), (int, float)) else None,
                scanlator=str(row.get("fansub", "")),
                uploaded_at=str(row["created"]) if row.get("created") is not None else None,
            )
            for row in response.json().get("result", [])
            if row.get("id") is not None
        ]

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        response = await self._request("GET", chapter_id)
        response.raise_for_status()
        return [
            SourcePage(
                source_id=row["url"],
                chapter_id=chapter_id,
                index=index,
                filename=row["url"].rsplit("/", 1)[-1].split("?", 1)[0] or f"{index}.jpg",
                source_name=self.name,
            )
            for index, row in enumerate(response.json().get("result", []), 1)
            if row.get("url")
        ]
