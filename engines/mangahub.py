"""Implementación GraphQL común de MangaHub."""

import json
import re

try:
    from .madara import MadaraSource, SourceChapter, SourcePage, SourceSeries
except ImportError:
    pass


class MangaHubSource(MadaraSource):
    manga_source = ""
    api_url = "https://api.mghcdn.com/graphql"
    image_url = "https://imgx.mghcdn.com"
    access_key = ""

    async def _key(self) -> str:
        if self.access_key:
            return self.access_key
        response = await self._request(
            "GET",
            f"{self.base_url}/chapter/martial-peak/chapter-1000",
            params={"reloadKey": 1},
        )
        cookie = response.headers.get("set-cookie", "") or response.headers.get("Set-Cookie", "")
        match = re.search(r"mhub_access=([^;]+)", cookie)
        if not match:
            raise RuntimeError("MangaHub access key not found")
        self.access_key = match.group(1)
        return self.access_key

    async def _graphql(self, query: str) -> dict:
        response = await self._request(
            "POST",
            self.api_url,
            json={"query": query},
            headers={
                "Content-Type": "application/json",
                "Origin": self.base_url,
                "x-mhub-access": await self._key(),
            },
        )
        response.raise_for_status()
        data = response.json()
        if data.get("errors"):
            self.access_key = ""
            raise RuntimeError("; ".join(item.get("message", "MangaHub API error") for item in data["errors"]))
        return data.get("data", {})

    @staticmethod
    def _quoted(value: str) -> str:
        return json.dumps(value, ensure_ascii=False)

    async def _catalog(self, query: str, order: str, page: int) -> list[SourceSeries]:
        gql = (
            "{search(x:%s,q:%s,genre:\"all\",mod:%s,offset:%d)"
            "{rows{title author slug image genres latestChapter}}}"
            % (self.manga_source, self._quoted(query), order, max(page - 1, 0) * 30)
        )
        rows = (await self._graphql(gql)).get("search", {}).get("rows", [])
        seen, result = set(), []
        for row in rows:
            signature = f"{row.get('author')}{row.get('latestChapter')}{row.get('genres')}"
            if not row.get("slug") or not row.get("title") or signature in seen:
                continue
            seen.add(signature)
            result.append(
                SourceSeries(
                    source_id=f"{self.base_url}/manga/{row['slug']}",
                    title=row["title"],
                    source_name=self.name,
                )
            )
        return result

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        return (await self._catalog(query.strip(), "POPULAR", 1))[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"}:
            return []
        return await self._catalog("", "POPULAR" if kind == "popular" else "LATEST", page)

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        slug = series_id.rstrip("/").rsplit("/", 1)[-1]
        gql = (
            "{manga(x:%s,slug:%s){slug chapters{number title date}}}"
            % (self.manga_source, self._quoted(slug))
        )
        manga = (await self._graphql(gql)).get("manga", {})
        result = []
        for row in reversed(manga.get("chapters") or []):
            number = float(row["number"])
            number_text = str(int(number)) if number.is_integer() else str(number)
            title = (row.get("title") or "").strip()
            if number_text not in title:
                title = f"Chapter {number_text}{f' - {title}' if title else ''}"
            result.append(
                SourceChapter(
                    source_id=f"{self.base_url}/chapter/{slug}/chapter-{number_text}",
                    title=title,
                    series_id=series_id,
                    source_name=self.name,
                    number=number,
                    uploaded_at=row.get("date"),
                )
            )
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        parts = chapter_id.rstrip("/").split("/")
        slug, number = parts[-2], float(parts[-1].removeprefix("chapter-"))
        gql = (
            "{chapter(x:%s,slug:%s,number:%s){pages mangaID number manga{slug}}}"
            % (self.manga_source, self._quoted(slug), number)
        )
        chapter_data = (await self._graphql(gql)).get("chapter", {})
        payload = chapter_data.get("pages") or "{}"
        data = json.loads(payload) if isinstance(payload, str) else payload
        prefix = data.get("p", "")
        return [
            SourcePage(
                source_id=f"{self.image_url}/{prefix}{filename}",
                chapter_id=chapter_id,
                index=index,
                filename=filename.rsplit("/", 1)[-1],
                source_name=self.name,
            )
            for index, filename in enumerate(data.get("i", []), 1)
        ]
