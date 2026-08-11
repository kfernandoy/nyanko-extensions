"""Implementación GraphQL común de Senkuro."""

try:
    from .base import FuenteBaseSource, SourceChapter, SourcePage, SourceSeries
except ImportError:
    pass


SEARCH_QUERY = """
query searchTachiyomiManga($query:String,$orderBy:MangaTachiyomiOrder,$offset:Int){
  mangaTachiyomiSearch(query:$query,orderBy:$orderBy,offset:$offset){
    mangas{id slug titles{lang content}}
  }
}"""
CHAPTERS_QUERY = """
query fetchTachiyomiChapters($mangaId:ID!){
  mangaTachiyomiChapters(mangaId:$mangaId){
    chapters{id slug name teamIds number volume createdAt updatedAt}
    teams{id name}
  }
}"""
PAGES_QUERY = """
query fetchTachiyomiChapterPages($mangaId:ID!,$chapterId:ID!){
  mangaTachiyomiChapterPages(mangaId:$mangaId,chapterId:$chapterId){pages{url}}
}"""


class SenkuroSource(FuenteBaseSource):
    requests_per_minute = 180

    @property
    def api_url(self) -> str:
        return self.base_url.replace("https://", "https://api.", 1) + "/graphql"

    async def _graphql(self, operation: str, query: str, variables: dict) -> dict:
        app_id = "4026531840100" if self.display_name == "Senkuro" else "5033164800100"
        response = await self._request(
            "POST",
            self.api_url,
            json={"operationName": operation, "query": query, "variables": variables},
            headers={
                "Content-Type": "application/json",
                "App-Id": app_id,
                "App-Version": "060626",
            },
        )
        response.raise_for_status()
        return response.json().get("data", {})

    def _series(self, rows: list[dict]) -> list[SourceSeries]:
        result: list[SourceSeries] = []
        for row in rows:
            titles = row.get("titles") or []
            title = next(
                (item.get("content", "") for lang in ("RU", "EN") for item in titles if item.get("lang") == lang),
                titles[0].get("content", "") if titles else "",
            )
            if row.get("id") and row.get("slug") and title:
                result.append(
                    SourceSeries(
                        source_id=f"{self.base_url}/manga/{row['slug']}#{row['id']}",
                        title=title,
                        source_name=self.name,
                    )
                )
        return result

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        data = await self._graphql(
            "searchTachiyomiManga",
            SEARCH_QUERY,
            {
                "query": query.strip() or None,
                "orderBy": {"direction": "DESC", "field": "POPULARITY_SCORE"},
                "offset": 0,
            },
        )
        return self._series(data.get("mangaTachiyomiSearch", {}).get("mangas", []))[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind != "popular":
            return []
        data = await self._graphql(
            "searchTachiyomiManga",
            SEARCH_QUERY,
            {
                "query": None,
                "orderBy": {"direction": "DESC", "field": "POPULARITY_SCORE"},
                "offset": max(page - 1, 0) * 10,
            },
        )
        return self._series(data.get("mangaTachiyomiSearch", {}).get("mangas", []))

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        website_url, marker, manga_id = series_id.rpartition("#")
        if not marker:
            return []
        slug = website_url.rstrip("/").rsplit("/", 1)[-1]
        data = await self._graphql(
            "fetchTachiyomiChapters",
            CHAPTERS_QUERY,
            {"mangaId": manga_id},
        )
        payload = data.get("mangaTachiyomiChapters", {})
        teams = {item["id"]: item.get("name", "") for item in payload.get("teams", [])}
        return [
            SourceChapter(
                source_id=f"{self.base_url}/manga/{slug}/chapters/{row['slug']}#{manga_id}/{row['id']}",
                title=f"{row.get('volume', '')}. Глава {row.get('number', '')} {row.get('name') or ''}".strip(),
                series_id=series_id,
                source_name=self.name,
                number=float(row["number"]) if str(row.get("number", "")).replace(".", "", 1).isdigit() else None,
                scanlator=", ".join(teams[key] for key in row.get("teamIds", []) if key in teams),
                uploaded_at=row.get("updatedAt") or row.get("createdAt"),
            )
            for row in payload.get("chapters", [])
            if row.get("id") and row.get("slug")
        ]

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        _, marker, ids = chapter_id.rpartition("#")
        manga_id, separator, api_chapter_id = ids.partition("/")
        if not marker or not separator:
            return []
        data = await self._graphql(
            "fetchTachiyomiChapterPages",
            PAGES_QUERY,
            {"mangaId": manga_id, "chapterId": api_chapter_id},
        )
        rows = data.get("mangaTachiyomiChapterPages", {}).get("pages", [])
        return [
            SourcePage(
                source_id=row["url"],
                chapter_id=chapter_id,
                index=index,
                filename=row["url"].rsplit("/", 1)[-1].split("?", 1)[0] or f"{index}.jpg",
                source_name=self.name,
            )
            for index, row in enumerate(rows, 1)
            if row.get("url")
        ]
