"""Implementación JSON común de HentaiHand."""

try:
    from .madara import MadaraSource, SourceChapter, SourcePage, SourceSeries
except ImportError:
    pass


class HentaiHandSource(MadaraSource):
    language_ids: list[int] = []

    def _language_params(self) -> dict[str, int]:
        return {
            f"languages[{-index - 1}]": value
            for index, value in enumerate(self.language_ids)
        }

    def _series(self, rows: list[dict]) -> list[SourceSeries]:
        return [
            SourceSeries(
                source_id=f"{self.base_url}/en/comic/{row['slug']}",
                title=row["title"],
                source_name=self.name,
            )
            for row in rows
            if row.get("slug") and row.get("title")
        ]

    async def _catalog(self, page: int, sort: str, query: str = "") -> list[SourceSeries]:
        params = {
            "page": max(page, 1),
            "sort": sort,
            "order": "desc",
            "duration": "all",
            **self._language_params(),
        }
        if query:
            params["q"] = query
        response = await self._request("GET", f"{self.base_url}/api/comics", params=params)
        response.raise_for_status()
        return self._series(response.json().get("data", []))

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        return (await self._catalog(1, "uploaded_at", query.strip()))[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"}:
            return []
        return await self._catalog(page, "popularity" if kind == "popular" else "uploaded_at")

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        slug = series_id.rstrip("/").rsplit("/", 1)[-1]
        response = await self._request("GET", f"{self.base_url}/api/comics/{slug}")
        response.raise_for_status()
        data = response.json()
        chapter_slug = data.get("slug") or slug
        return [
            SourceChapter(
                source_id=f"{self.base_url}/api/comics/{chapter_slug}/images",
                title="Chapter",
                series_id=series_id,
                source_name=self.name,
                number=1.0,
                uploaded_at=data.get("updated_at"),
            )
        ]

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        response = await self._request("GET", chapter_id)
        response.raise_for_status()
        return [
            SourcePage(
                source_id=row["source_url"],
                chapter_id=chapter_id,
                index=int(row.get("page", index)),
                filename=row["source_url"].rsplit("/", 1)[-1].split("?", 1)[0],
                source_name=self.name,
            )
            for index, row in enumerate(response.json().get("images", []), 1)
            if row.get("source_url")
        ]
