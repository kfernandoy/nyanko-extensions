"""Implementación común de MangAdventure v2."""

try:
    from .madara import MadaraSource, SourceChapter, SourcePage, SourceSeries
except ImportError:
    pass


class MangAdventureSource(MadaraSource):
    @property
    def api_url(self) -> str:
        return f"{self.base_url}/api/v2"

    def _series(self, payload: dict) -> list[SourceSeries]:
        return [
            SourceSeries(
                source_id=f"{self.base_url}/reader/{row['slug']}",
                title=str(row["title"]).strip(),
                source_name=self.name,
            )
            for row in payload.get("results", [])
            if row.get("slug") and row.get("title")
        ]

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        response = await self._request(
            "GET",
            f"{self.api_url}/series",
            params={"page": "1", "title": query.strip()},
        )
        response.raise_for_status()
        return self._series(response.json())[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"}:
            return []
        response = await self._request(
            "GET",
            f"{self.api_url}/series",
            params={"page": str(page), "sort": "-views" if kind == "popular" else "-latest_upload"},
        )
        response.raise_for_status()
        return self._series(response.json())

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        slug = series_id.rstrip("/").rsplit("/", 1)[-1]
        response = await self._request(
            "GET",
            f"{self.api_url}/series/{slug}/chapters",
            params={"date_format": "timestamp"},
        )
        response.raise_for_status()
        return [
            SourceChapter(
                source_id=f"{self.api_url}/chapters/{row['id']}/read",
                title=f"{row.get('full_title') or row.get('title') or 'Chapter'}{' [END]' if row.get('final') else ''}",
                series_id=series_id,
                source_name=self.name,
                number=float(row["number"]) if isinstance(row.get("number"), (int, float)) else None,
                scanlator=", ".join(row.get("groups") or []),
                uploaded_at=str(row["published"]) if row.get("published") is not None else None,
            )
            for row in response.json().get("results", [])
            if row.get("id") is not None
        ]

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        chapter_number = chapter_id.split("/chapters/", 1)[-1].split("/", 1)[0]
        response = await self._request(
            "GET",
            f"{self.api_url}/chapters/{chapter_number}/pages",
            params={"track": "true"},
        )
        response.raise_for_status()
        return [
            SourcePage(
                source_id=row["image"],
                chapter_id=chapter_id,
                index=int(row.get("number", index)),
                filename=row["image"].rsplit("/", 1)[-1].split("?", 1)[0] or f"{index}.jpg",
                source_name=self.name,
            )
            for index, row in enumerate(response.json().get("results", []), 1)
            if row.get("image")
        ]
