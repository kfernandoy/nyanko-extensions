"""Implementación común de Bakkin Reader X para Nyanko Source v3."""

from urllib.parse import urljoin

try:
    from .madara import MadaraSource, SourceChapter, SourcePage, SourceSeries
except ImportError:
    pass


class BakkinSource(MadaraSource):
    supports_latest = False

    def __init__(self, fetcher=None) -> None:
        super().__init__(fetcher)
        self._series_cache: list[dict] | None = None

    async def _series(self) -> list[dict]:
        if self._series_cache is None:
            response = await self._request("GET", urljoin(self.base_url, "main.php"))
            response.raise_for_status()
            payload = response.json()
            self._series_cache = list(payload.values()) if isinstance(payload, dict) else payload
        return self._series_cache

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        query = query.casefold().strip()
        return [
            SourceSeries(
                source_id=item["dir"],
                title=item.get("name") or item["dir"],
                source_name=self.name,
            )
            for item in await self._series()
            if query in (item.get("name") or item["dir"]).casefold()
        ][:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind != "popular" or page != 1:
            return []
        return await self.search("", 10_000)

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        manga = next((item for item in await self._series() if item.get("dir") == series_id), None)
        if manga is None:
            return []
        result: list[SourceChapter] = []
        for volume in manga.get("volumes", []):
            for chapter in volume.get("chapters", []):
                source_id = f"{series_id}/{volume['dir']}/{chapter['dir']}"
                title = f"{volume.get('name') or volume['dir']} - {chapter.get('name') or chapter['dir']}"
                raw_number = chapter.get("dir", "").rsplit("c", 1)[-1]
                try:
                    number = float(raw_number)
                except ValueError:
                    number = None
                result.append(
                    SourceChapter(
                        source_id=source_id,
                        title=title,
                        series_id=series_id,
                        source_name=self.name,
                        number=number,
                    )
                )
        return list(reversed(result))

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        parts = chapter_id.split("/")
        if len(parts) != 3:
            return []
        manga = next((item for item in await self._series() if item.get("dir") == parts[0]), None)
        volume = next(
            (item for item in manga.get("volumes", []) if item.get("dir") == parts[1]),
            None,
        ) if manga else None
        selected = next(
            (item for item in volume.get("chapters", []) if item.get("dir") == parts[2]),
            None,
        ) if volume else None
        urls = [urljoin(self.base_url, path) for path in selected.get("pages", [])] if selected else []
        return [
            SourcePage(
                source_id=url,
                chapter_id=chapter_id,
                index=index,
                filename=url.rsplit("/", 1)[-1] or f"{index}.jpg",
                source_name=self.name,
            )
            for index, url in enumerate(urls, 1)
        ]
