"""Implementación tRPC común del tema Hiper."""

from urllib.parse import urlparse

try:
    from .madara import MadaraSource, SourceChapter, SourcePage, SourceSeries
except ImportError:
    pass


class HiperSource(MadaraSource):
    requests_per_minute = 180
    extra_headers: dict[str, str] = {}

    def __init__(self, fetcher=None) -> None:
        super().__init__(fetcher)
        self.capabilities.headers.update(self.extra_headers)

    async def _trpc(self, procedures: str, payload: dict) -> list[dict]:
        response = await self._request(
            "GET",
            f"{self.base_url}/api/trpc/{procedures}",
            params={"batch": "1", "input": __import__("json").dumps(payload, separators=(",", ":"))},
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _result(rows: list[dict], position: int = -1):
        return rows[position].get("result", {}).get("data", {}).get("json")

    def _series(self, rows: list[dict]) -> list[SourceSeries]:
        result: list[SourceSeries] = []
        for row in rows:
            if not all((row.get("id"), row.get("slug"), row.get("title"))):
                continue
            result.append(
                SourceSeries(
                    source_id=f"{self.base_url}/manga/{row['slug']}#{row['id']}",
                    title=row["title"],
                    source_name=self.name,
                )
            )
        return result

    async def _catalog(self, query: str, sort: str, page: int) -> list[SourceSeries]:
        undefined = ["undefined"]
        payload = {
            "0": {
                "json": {
                    "q": query,
                    "sort": sort,
                    "filters": {
                        "genres": None,
                        "type": None,
                        "status": None,
                        "contentRating": None,
                        "author": None,
                        "artist": None,
                        "year": None,
                    },
                    "limit": 30,
                    "offset": max(page - 1, 0) * 30,
                    "maxRating": "pornographic",
                },
                "meta": {
                    "values": {
                        "filters.genres": undefined,
                        "filters.type": undefined,
                        "filters.status": undefined,
                        "filters.contentRating": undefined,
                        "filters.author": undefined,
                        "filters.artist": undefined,
                        "filters.year": undefined,
                    }
                },
            }
        }
        data = await self._trpc("search.query", payload)
        return self._series((self._result(data, 0) or {}).get("hits", []))

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        return (await self._catalog(query.strip(), "popular", 1))[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"}:
            return []
        return await self._catalog("", "popular" if kind == "popular" else "recent", page)

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        website_url, marker, manga_id = series_id.rpartition("#")
        if not marker or not manga_id.isdigit():
            return []
        slug = urlparse(website_url).path.rstrip("/").rsplit("/", 1)[-1]
        payload = {
            "0": {"json": {"values": ["undefined"]}},
            "1": {
                "json": {
                    "seriesId": int(manga_id),
                    "chapterId": None,
                    "sort": "best",
                    "page": 1,
                    "limit": 20,
                },
                "meta": {"values": {"chapterId": ["undefined"]}},
            },
            "2": {"json": {"seriesId": int(manga_id)}},
        }
        rows = self._result(await self._trpc("auth.me,series.chapters", payload)) or []
        result: list[SourceChapter] = []
        for row in rows:
            number = row.get("number")
            if number is None:
                continue
            number_text = str(number).removesuffix(".0")
            title = row.get("title")
            result.append(
                SourceChapter(
                    source_id=f"{self.base_url}/manga/{slug}/{number_text}",
                    title=title if title and any(char.isdigit() for char in title) else f"Chapter {number_text}{f' {title}' if title else ''}",
                    series_id=series_id,
                    source_name=self.name,
                    number=float(number),
                    uploaded_at=row.get("createdAt"),
                )
            )
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        parts = urlparse(chapter_id).path.strip("/").split("/")
        if len(parts) < 3:
            return []
        slug, number = parts[-2:]
        undefined = {"json": None, "meta": {"values": ["undefined"]}}
        payload = {
            "0": undefined,
            "1": {"json": {"slug": slug}},
            "2": {"json": {"seriesSlug": slug, "chapterNumber": float(number)}},
            "3": {"json": {"position": "footer_bottom"}},
        }
        rows = self._result(
            await self._trpc("auth.me,series.bySlug,reader.chapterPages", payload)
        ) or []
        return [
            SourcePage(
                source_id=row.get("avifUrl") or row["webpUrl"],
                chapter_id=chapter_id,
                index=int(row.get("pageOrder", index)),
                filename=(row.get("avifUrl") or row["webpUrl"]).rsplit("/", 1)[-1].split("?", 1)[0],
                source_name=self.name,
            )
            for index, row in enumerate(rows, 1)
            if row.get("avifUrl") or row.get("webpUrl")
        ]
