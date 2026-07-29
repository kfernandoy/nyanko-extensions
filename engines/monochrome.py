"""Implementación común de Monochrome CMS para Nyanko Source v3."""

try:
    from .madara import MadaraSource, SourceChapter, SourcePage, SourceSeries
except ImportError:
    pass


class MonochromeSource(MadaraSource):
    api_url = ""
    supports_latest = False

    def __init__(self, fetcher=None) -> None:
        if not self.api_url:
            self.api_url = self.base_url.replace("://", "://api.", 1)
        super().__init__(fetcher)

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        response = await self._request(
            "GET",
            f"{self.api_url}/manga",
            params={"limit": str(limit), "offset": "0", "title": query.strip()},
        )
        response.raise_for_status()
        payload = response.json()
        return [
            SourceSeries(source_id=item["id"], title=item["title"], source_name=self.name)
            for item in payload.get("results", [])
            if item.get("id") and item.get("title")
        ]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind != "popular":
            return []
        response = await self._request(
            "GET",
            f"{self.api_url}/manga",
            params={"limit": "10", "offset": str(10 * max(page - 1, 0)), "title": ""},
        )
        response.raise_for_status()
        return [
            SourceSeries(source_id=item["id"], title=item["title"], source_name=self.name)
            for item in response.json().get("results", [])
            if item.get("id") and item.get("title")
        ]

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        response = await self._request("GET", f"{self.api_url}/manga/{series_id}/chapters")
        response.raise_for_status()
        result: list[SourceChapter] = []
        for item in response.json():
            chapter_id = item.get("id")
            length = item.get("length")
            if not chapter_id or not isinstance(length, int):
                continue
            number = float(item.get("number", 0))
            name = item.get("name", "")
            title = f"Chapter {number:g}" + (f" - {name}" if name else "")
            result.append(
                SourceChapter(
                    source_id=f"{chapter_id}|{item.get('version', 0)}|{length}",
                    title=title,
                    series_id=series_id,
                    source_name=self.name,
                    number=number,
                    scanlator=item.get("scanGroup", ""),
                    uploaded_at=item.get("uploadTime"),
                )
            )
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        uuid, version, raw_length = chapter_id.split("|", 2)
        urls = [
            f"{self.api_url}/media/{uuid}/{index}.jpg?version={version}"
            for index in range(1, int(raw_length) + 1)
        ]
        return [
            SourcePage(
                source_id=url,
                chapter_id=chapter_id,
                index=index,
                filename=f"{index}.jpg",
                source_name=self.name,
            )
            for index, url in enumerate(urls, 1)
        ]
