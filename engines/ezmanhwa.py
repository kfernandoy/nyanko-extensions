"""Implementación común de EZManhwa para capítulos públicos."""

try:
    from .madara import MadaraSource, SourceChapter, SourcePage, SourceSeries
except ImportError:
    pass


class EZManhwaSource(MadaraSource):
    api_url = ""
    requests_per_minute = 120

    def _series(self, payload: dict) -> list[SourceSeries]:
        return [
            SourceSeries(
                source_id=f"{self.base_url}/series/{row['slug']}",
                title=str(row["title"]).strip(),
                source_name=self.name,
            )
            for row in payload.get("data", [])
            if row.get("slug") and row.get("title") and row.get("type") != "NOVEL"
        ]

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        response = await self._request(
            "GET",
            f"{self.api_url}/series/search",
            params={"page": "1", "perPage": "20", "q": query.strip()},
        )
        response.raise_for_status()
        return self._series(response.json())[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"}:
            return []
        response = await self._request(
            "GET",
            f"{self.api_url}/series",
            params={"page": str(page), "perPage": "20", "sort": kind},
        )
        response.raise_for_status()
        return self._series(response.json())

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        slug = series_id.rstrip("/").rsplit("/", 1)[-1]
        page = 1
        rows: list[dict] = []
        while True:
            response = await self._request(
                "GET",
                f"{self.api_url}/series/{slug}/chapters",
                params={"page": str(page), "perPage": "100", "sort": "desc"},
            )
            response.raise_for_status()
            payload = response.json()
            rows.extend(payload.get("data", []))
            if page >= int(payload.get("totalPages", 1)):
                break
            page += 1
        result: list[SourceChapter] = []
        for row in rows:
            if row.get("requiresPurchase") is True or not row.get("slug"):
                continue
            number = row.get("number")
            number_text = f"{number:g}" if isinstance(number, (int, float)) else ""
            label = str(row.get("title", "")).strip()
            title = f"Chapter {number_text}".strip() if number_text else label or "Chapter"
            if label and label != number_text and label.casefold() not in title.casefold():
                title += f" - {label}"
            result.append(
                SourceChapter(
                    source_id=f"{self.api_url}/series/{slug}/chapters/{row['slug']}",
                    title=title,
                    series_id=series_id,
                    source_name=self.name,
                    number=float(number) if isinstance(number, (int, float)) else None,
                    uploaded_at=row.get("createdAt"),
                )
            )
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        response = await self._request("GET", chapter_id)
        response.raise_for_status()
        payload = response.json()
        if payload.get("requiresPurchase") is True:
            return []
        return [
            SourcePage(
                source_id=row["url"],
                chapter_id=chapter_id,
                index=index,
                filename=row["url"].rsplit("/", 1)[-1].split("?", 1)[0] or f"{index}.jpg",
                source_name=self.name,
            )
            for index, row in enumerate(payload.get("images") or [], 1)
            if row.get("url")
        ]
