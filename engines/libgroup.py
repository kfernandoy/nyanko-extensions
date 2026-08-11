"""Implementación JSON común de MangaLib/HentaiLib/SlashLib."""

from urllib.parse import urljoin

try:
    from .base import FuenteBaseSource, SourceChapter, SourcePage, SourceSeries
except ImportError:
    pass


class LibGroupSource(FuenteBaseSource):
    api_url = "https://api.cdnlibs.org"
    site_id = 1
    requests_per_minute = 60

    def __init__(self, fetcher=None) -> None:
        super().__init__(fetcher)
        self.capabilities.headers["Site-Id"] = str(self.site_id)

    def _series(self, rows: list[dict]) -> list[SourceSeries]:
        return [
            SourceSeries(
                source_id=f"{self.base_url}/{row['slug_url']}",
                title=row.get("eng_name") or row.get("rus_name") or row["name"],
                source_name=self.name,
            )
            for row in rows
            if row.get("slug_url") and (row.get("name") or row.get("eng_name") or row.get("rus_name"))
        ]

    async def _catalog(self, page: int, query: str = "", latest: bool = False) -> list[SourceSeries]:
        endpoint = "latest-updates" if latest else "manga"
        params = {"page": max(page, 1)}
        if not latest:
            params["site_id[]"] = self.site_id
        if query:
            params["q"] = query
        response = await self._request("GET", f"{self.api_url}/api/{endpoint}", params=params)
        response.raise_for_status()
        return self._series(response.json().get("data", []))

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        return (await self._catalog(1, query.strip()))[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"}:
            return []
        return await self._catalog(page, latest=kind == "latest")

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        slug = series_id.rstrip("/").rsplit("/", 1)[-1]
        if "--" not in slug:
            return []
        response = await self._request("GET", f"{self.api_url}/api/manga/{slug}/chapters")
        response.raise_for_status()
        rows = response.json().get("data", [])
        result: list[SourceChapter] = []
        for row in rows:
            branch = next(
                (item for item in row.get("branches", []) if (item.get("restricted_view") or {}).get("is_open", True)),
                None,
            )
            if row.get("branches") and branch is None:
                continue
            volume, number = str(row.get("volume", "")), str(row.get("number", ""))
            branch_id = branch.get("branch_id") if branch else None
            suffix = f"&branch_id={branch_id}" if branch_id is not None else ""
            title = f"Том {volume}. Глава {number}"
            if row.get("name"):
                title += f" - {row['name']}"
            result.append(
                SourceChapter(
                    source_id=f"{self.api_url}/api/manga/{slug}/chapter?volume={volume}&number={number}{suffix}",
                    title=title,
                    series_id=series_id,
                    source_name=self.name,
                    number=float(number),
                    uploaded_at=branch.get("created_at") if branch else None,
                )
            )
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        response = await self._request("GET", chapter_id)
        response.raise_for_status()
        rows = response.json().get("data", {}).get("pages", [])
        relative = any(row.get("url") and not row["url"].startswith("http") for row in rows)
        image_base = ""
        if relative:
            constants = await self._request(
                "GET",
                f"{self.api_url}/api/constants",
                params={"fields[]": "imageServers"},
            )
            constants.raise_for_status()
            servers = constants.json().get("data", {}).get("imageServers", [])
            image_base = next(
                (item["url"] for item in servers if self.site_id in item.get("site_ids", [])),
                "",
            )
        return [
            SourcePage(
                source_id=row["url"] if row["url"].startswith("http") else urljoin(f"{image_base}/", row["url"].lstrip("/")),
                chapter_id=chapter_id,
                index=int(row.get("slug", index)),
                filename=row["url"].rsplit("/", 1)[-1],
                source_name=self.name,
            )
            for index, row in enumerate(rows, 1)
            if row.get("url")
        ]
