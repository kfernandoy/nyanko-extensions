"""Implementación común de la API Iken para Nyanko Source v3."""

import re

try:
    from .madara import (
        MadaraSource,
        SourceChapter,
        SourcePage,
        SourceSeries,
    )
except ImportError:
    pass


class IkenSource(MadaraSource):
    per_page = 18
    sort_pages_by_filename = False
    use_chapters_api = False

    @property
    def api_url(self) -> str:
        return self.base_url.replace("https://", "https://api.", 1)

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        return (await self._query(1, query.strip()))[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"}:
            return []
        return await self._query(
            page,
            "",
            "totalViews" if kind == "popular" else "lastChapterAddedAt",
        )

    async def _query(self, page: int, query: str, order: str = "") -> list[SourceSeries]:
        params = {
            "page": page,
            "perPage": self.per_page,
            "searchTerm": query,
        }
        if order:
            params.update({"orderBy": order, "orderDirection": "desc"})
        response = await self._request("GET", f"{self.api_url}/api/query", params=params)
        response.raise_for_status()
        result: list[SourceSeries] = []
        for post in response.json().get("posts", []):
            if post.get("isNovel") or str(post.get("seriesType", "")).casefold() == "novel":
                continue
            result.append(
                SourceSeries(
                    source_id=f"{post['slug']}#{post['id']}",
                    title=post.get("postTitle") or "Sin título",
                    source_name=self.name,
                )
            )
        return result

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        slug, _, post_id = series_id.rpartition("#")
        if self.use_chapters_api:
            response = await self._request(
                "GET",
                f"{self.api_url}/api/chapters",
                params={"postId": post_id},
            )
        else:
            response = await self._request(
                "GET",
                f"{self.api_url}/api/post",
                params={"postSlug": slug},
            )
        response.raise_for_status()
        post = response.json().get("post") or {}
        result: list[SourceChapter] = []
        for chapter in post.get("chapters", []):
            if not chapter.get("isAccessible"):
                continue
            number = chapter.get("number")
            title = f"Chapter {number}"
            if chapter.get("title"):
                title += f" - {chapter['title']}"
            creator = chapter.get("createdBy") or {}
            result.append(
                SourceChapter(
                    source_id=f"/series/{slug}/{chapter['slug']}#{chapter['id']}",
                    title=title,
                    series_id=series_id,
                    source_name=self.name,
                    number=float(number) if number is not None else None,
                    scanlator=creator.get("name") or "",
                    uploaded_at=chapter.get("createdAt") or None,
                )
            )
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        _, _, raw_id = chapter_id.rpartition("#")
        response = await self._request(
            "GET",
            f"{self.api_url}/api/chapter",
            params={"chapterId": raw_id},
        )
        response.raise_for_status()
        data = response.json().get("chapter") or {}
        if any(
            data.get(key)
            for key in ("isPermanentlyLocked", "isLockedByCoins", "isShortLinkLocked")
        ):
            return []
        images = data.get("images", [])
        if self.sort_pages_by_filename:
            images.sort(
                key=lambda page: int(match.group()) if (match := re.search(r"\d+", page["url"].rsplit("/", 1)[-1])) else 10**9
            )
        else:
            images.sort(key=lambda page: page.get("order") if page.get("order") is not None else 10**9)
        return [
            SourcePage(
                source_id=page["url"].replace(" ", "%20"),
                chapter_id=chapter_id,
                index=index,
                filename=page["url"].rsplit("/", 1)[-1].split("?", 1)[0] or f"{index}.jpg",
                source_name=self.name,
            )
            for index, page in enumerate(images, 1)
        ]
