"""Implementación común de la API Guya/Cubari para Nyanko Source v3."""

try:
    from .madara import (
        MadaraSource,
        SourceChapter,
        SourcePage,
        SourceSeries,
    )
except ImportError:
    pass


class GuyaSource(MadaraSource):
    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        response = await self._request("GET", f"{self.base_url}/api/get_all_series/")
        response.raise_for_status()
        query = query.casefold().strip()
        return [
            series
            for series in self._series(response.json())
            if query in series.title.casefold()
        ][:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"} or page != 1:
            return []
        response = await self._request("GET", f"{self.base_url}/api/get_all_series/")
        response.raise_for_status()
        payload = response.json()
        rows = list(payload.items())
        if kind == "latest":
            rows.sort(key=lambda item: item[1].get("last_updated", 0), reverse=True)
        return self._series(dict(rows))

    def _series(self, payload: dict) -> list[SourceSeries]:
        return [
            SourceSeries(
                source_id=data["slug"],
                title=title or data.get("title") or "Sin título",
                source_name=self.name,
            )
            for title, data in payload.items()
            if isinstance(data, dict) and data.get("slug")
        ]

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        response = await self._request("GET", f"{self.base_url}/api/series/{series_id}/")
        response.raise_for_status()
        payload = response.json()
        group_names = payload.get("groups", {})
        preferred = payload.get("preferred_sort", [])
        result: list[SourceChapter] = []
        for number, chapter in (payload.get("chapters") or {}).items():
            groups = chapter.get("groups") or {}
            group_id = next((str(item) for item in preferred if str(item) in groups), None)
            if group_id is None:
                group_id = next(iter(groups), "")
            if not group_id:
                continue
            title = f"{number} - {chapter.get('title', '')}".rstrip(" -")
            released = (chapter.get("release_date") or {}).get(group_id)
            result.append(
                SourceChapter(
                    source_id=f"{series_id}/{number}|{group_id}",
                    title=title,
                    series_id=series_id,
                    source_name=self.name,
                    number=float(number) if self._is_number(number) else None,
                    scanlator=str(group_names.get(group_id, group_id)),
                    uploaded_at=str(released) if released is not None else None,
                )
            )
        return list(reversed(result))

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        path, _, group_id = chapter_id.rpartition("|")
        slug, _, number = path.rpartition("/")
        response = await self._request("GET", f"{self.base_url}/api/series/{slug}/")
        response.raise_for_status()
        chapter_data = response.json()["chapters"][number]
        folder = chapter_data["folder"]
        filenames = chapter_data["groups"].get(group_id, [])
        urls = [
            f"{self.base_url}/media/manga/{slug}/chapters/{folder}/{group_id}/{filename}"
            for filename in filenames
        ]
        return [
            SourcePage(
                source_id=url,
                chapter_id=chapter_id,
                index=index,
                filename=url.rsplit("/", 1)[-1],
                source_name=self.name,
            )
            for index, url in enumerate(urls, 1)
        ]

    @staticmethod
    def _is_number(value: str) -> bool:
        try:
            float(value)
            return True
        except ValueError:
            return False
