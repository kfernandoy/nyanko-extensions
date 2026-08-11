"""Implementación común de Kemono y Coomer."""

from datetime import datetime

try:
    from .base import FuenteBaseSource, SourceChapter, SourcePage, SourceSeries
except ImportError:
    pass


def _updated(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0


class KemonoSource(FuenteBaseSource):
    requests_per_minute = 60

    async def _creators(self) -> list[dict]:
        response = await self._request("GET", f"{self.base_url}/api/v1/creators")
        response.raise_for_status()
        return [
            row
            for row in response.json()
            if str(row.get("service", "")).lower() != "discord"
        ]

    def _series(self, rows: list[dict]) -> list[SourceSeries]:
        return [
            SourceSeries(
                source_id=f"{self.base_url}/{row['service']}/user/{row['id']}",
                title=str(row["name"]).strip(),
                source_name=self.name,
            )
            for row in rows
            if row.get("id") is not None and row.get("service") and row.get("name")
        ]

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        needle = query.strip().casefold()
        rows = [row for row in await self._creators() if needle in str(row.get("name", "")).casefold()]
        return self._series(rows)[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"}:
            return []
        rows = await self._creators()
        key = (lambda row: int(row.get("favorited", -1))) if kind == "popular" else (lambda row: _updated(row.get("updated")))
        rows.sort(key=key, reverse=True)
        start = max(page - 1, 0) * 50
        return self._series(rows[start : start + 50])

    @staticmethod
    def _images(post: dict) -> list[tuple[str, str]]:
        rows = []
        file = post.get("file") or {}
        if file.get("path"):
            rows.append(file)
        rows.extend(post.get("attachments") or [])
        seen: set[str] = set()
        result: list[tuple[str, str]] = []
        for row in rows:
            path = str(row.get("path", ""))
            if path in seen or path.rsplit(".", 1)[-1].lower() not in {"png", "jpg", "jpeg", "gif", "webp"}:
                continue
            seen.add(path)
            result.append((path, str(row.get("name") or "")))
        return result

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        path = series_id.split(self.base_url, 1)[-1]
        response = await self._request("GET", f"{self.base_url}/api/v1{path}/posts", params={"o": "0"})
        response.raise_for_status()
        return [
            SourceChapter(
                source_id=f"{self.base_url}/api/v1/{row['service']}/user/{row['user']}/post/{row['id']}",
                title=str(row.get("title", "")).strip() or "Post",
                series_id=series_id,
                source_name=self.name,
                uploaded_at=row.get("edited") or row.get("published") or row.get("added"),
            )
            for row in response.json()
            if row.get("id") is not None
            and row.get("service")
            and row.get("user") is not None
            and self._images(row)
        ]

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        response = await self._request("GET", chapter_id)
        response.raise_for_status()
        post = response.json().get("post", {})
        return [
            SourcePage(
                source_id=f"{self.base_url}/data{path}" + (f"?f={name}" if name else ""),
                chapter_id=chapter_id,
                index=index,
                filename=name or path.rsplit("/", 1)[-1] or f"{index}.jpg",
                source_name=self.name,
            )
            for index, (path, name) in enumerate(self._images(post), 1)
        ]
