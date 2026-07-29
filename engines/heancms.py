"""Implementación común de HeanCms para capítulos públicos."""

try:
    from .madara import MadaraSource, SourceChapter, SourcePage, SourceSeries
except ImportError:
    pass


class HeanCmsSource(MadaraSource):
    new_query = False
    latest_order = "desc"
    requests_per_minute = 60

    @property
    def api_url(self) -> str:
        return self.base_url.replace("://", "://api.", 1)

    def _series(self, payload: dict) -> list[SourceSeries]:
        result: list[SourceSeries] = []
        for row in payload.get("data", []):
            slug = str(row.get("series_slug", "")).strip()
            title = str(row.get("title", "")).strip()
            manga_id = row.get("id")
            if slug and title and manga_id is not None:
                result.append(
                    SourceSeries(
                        source_id=f"{self.base_url}/series/{slug}#{manga_id}",
                        title=title,
                        source_name=self.name,
                    )
                )
        return result

    async def _query(self, query: str, order_by: str, order: str, page: int = 1) -> list[SourceSeries]:
        response = await self._request(
            "GET",
            f"{self.api_url}/query",
            params={
                "query_string": query,
                "status" if self.new_query else "series_status": "All",
                "order": order,
                "orderBy": order_by,
                "series_type": "Comic",
                "page": str(page),
                "perPage": "12",
                "tags_ids": "[]",
                "adult": "true",
            },
        )
        response.raise_for_status()
        return self._series(response.json())

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        return (await self._query(query.strip(), "total_views", "desc"))[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind == "popular":
            return await self._query("", "total_views", "desc", page)
        if kind == "latest":
            return await self._query("", "latest", self.latest_order, page)
        return []

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        website_url, marker, manga_id = series_id.rpartition("#")
        if not marker or not manga_id:
            return []
        slug = website_url.rstrip("/").rsplit("/", 1)[-1]
        page = 1
        rows: list[dict] = []
        while True:
            response = await self._request(
                "GET",
                f"{self.api_url}/chapter/query",
                params={"page": str(page), "perPage": "100", "series_id": manga_id},
            )
            response.raise_for_status()
            payload = response.json()
            rows.extend(payload.get("data", []))
            meta = payload.get("meta", {})
            if int(meta.get("current_page", page)) >= int(meta.get("last_page", page)):
                break
            page += 1
        result: list[SourceChapter] = []
        for row in rows:
            if row.get("price") != 0:
                continue
            chapter_id = row.get("id")
            chapter_slug = str(row.get("chapter_slug", "")).strip()
            if chapter_id is None or not chapter_slug:
                continue
            title = str(row.get("chapter_name", "")).strip() or "Chapter"
            if row.get("chapter_title"):
                title += f" - {str(row['chapter_title']).strip()}"
            result.append(
                SourceChapter(
                    source_id=f"{self.base_url}/series/{slug}/{chapter_slug}#{chapter_id}",
                    title=title,
                    series_id=series_id,
                    source_name=self.name,
                    uploaded_at=row.get("created_at"),
                )
            )
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        website_url, marker, api_id = chapter_id.rpartition("#")
        if not marker or not api_id:
            return []
        chapter_path = website_url.split("/series/", 1)[-1]
        response = await self._request("GET", f"{self.api_url}/chapter/{chapter_path}#{api_id}")
        response.raise_for_status()
        payload = response.json()
        urls = payload.get("chapter", {}).get("chapter_data", {}).get("images", [])
        return [
            SourcePage(
                source_id=url if str(url).startswith(("http://", "https://")) else f"{self.api_url}/{str(url).lstrip('/')}",
                chapter_id=chapter_id,
                index=index,
                filename=str(url).rsplit("/", 1)[-1].split("?", 1)[0] or f"{index}.jpg",
                source_name=self.name,
            )
            for index, url in enumerate(urls, 1)
            if url
        ]
