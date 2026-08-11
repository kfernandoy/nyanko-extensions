"""Implementación común de MangaTaro."""

import hashlib
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

try:
    from .base import FuenteBaseSource, SourceChapter, SourcePage, SourceSeries
except ImportError:
    pass


class MangaTaroSource(FuenteBaseSource):
    def _series(self, rows: list[dict]) -> list[SourceSeries]:
        result: list[SourceSeries] = []
        for row in rows:
            if row.get("type") == "Novel" or not row.get("id"):
                continue
            slug = str(row.get("slug") or "").strip()
            if not slug and row.get("url"):
                parts = [part for part in urlparse(row["url"]).path.split("/") if part]
                if len(parts) >= 2 and parts[0] in {"manga", "read"}:
                    slug = parts[1]
            title = str(row.get("title", "")).strip()
            if slug and title:
                result.append(
                    SourceSeries(
                        source_id=f"{self.base_url}/manga/{slug}#{row['id']}",
                        title=title,
                        source_name=self.name,
                    )
                )
        return result

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        response = await self._request(
            "POST",
            f"{self.base_url}/auth/search",
            json={"query": query.strip(), "limit": min(limit, 25)},
        )
        response.raise_for_status()
        return self._series(response.json().get("results", []))[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"}:
            return []
        response = await self._request(
            "POST",
            f"{self.base_url}/wp-json/manga/v1/load",
            json={
                "page": page,
                "search": "",
                "years": "[]",
                "genres": "[]",
                "types": "[]",
                "statuses": "[]",
                "sort": "views_desc" if kind == "popular" else "latest",
                "genreMatchMode": "all",
            },
        )
        response.raise_for_status()
        return self._series(response.json())

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        website_url, marker, manga_id = series_id.rpartition("#")
        if not marker or not manga_id:
            return []
        timestamp = int(time.time())
        hour = datetime.now(timezone.utc).strftime("%Y%m%d%H")
        token = hashlib.md5(f"{timestamp}mng_ch_{hour}".encode()).hexdigest()[:16]
        response = await self._request(
            "GET",
            f"{self.base_url}/auth/manga-chapters",
            params={
                "manga_id": manga_id,
                "offset": "0",
                "limit": "9999",
                "order": "DESC",
                "_t": token,
                "_ts": str(timestamp),
            },
        )
        response.raise_for_status()
        return [
            SourceChapter(
                source_id=f"{self.base_url}{row['url'].rstrip('/')}",
                title=f"Chapter {row.get('chapter', '')}" + (f": {row['title']}" if row.get("title") not in {None, "", "N/A", "—"} else ""),
                series_id=series_id,
                source_name=self.name,
                number=float(row["chapter"]) if str(row.get("chapter", "")).replace(".", "", 1).isdigit() else None,
                scanlator=str(row.get("group_name") or ""),
            )
            for row in response.json().get("chapters", [])
            if row.get("url") and str(row.get("language", "")).casefold() == self.language.casefold()
        ]

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        slug = urlparse(chapter_id).path.rstrip("/").rsplit("/", 1)[-1]
        api_id = slug.rsplit("-", 1)[-1]
        response = await self._request(
            "GET",
            f"{self.base_url}/auth/chapter-content",
            params={"chapter_id": api_id},
        )
        response.raise_for_status()
        return [
            SourcePage(
                source_id=url,
                chapter_id=chapter_id,
                index=index,
                filename=url.rsplit("/", 1)[-1].split("?", 1)[0] or f"{index}.jpg",
                source_name=self.name,
            )
            for index, url in enumerate(response.json().get("images", []), 1)
            if url
        ]
