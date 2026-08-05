"""Implementación común de la API Iken."""

import re

try:
    from .madara import (
        MadaraSource,
        SourceChapter,
        SourceFilter,
        SourcePage,
        SourcePreference,
        SourceSeries,
        _parse_html,
    )
except ImportError:
    pass


class IkenSource(MadaraSource):
    per_page = 18
    sort_pages_by_filename = False
    use_chapters_api = False
    show_locked_chapters = False

    def __init__(self, fetcher=None) -> None:
        super().__init__(fetcher)
        self._genres: list[tuple[str, str]] | None = None
        self._next_pages: dict[tuple[tuple[str, str], ...], int] = {}

    @property
    def api_url(self) -> str:
        return self.base_url.replace("https://", "https://api.", 1)

    def get_preferences(self) -> list[SourcePreference]:
        return [SourcePreference(
            "pref_show_locked_chapters", "Show inaccessible chapters", "checkbox", default=False,
        )]

    async def get_filters(self) -> list[SourceFilter]:
        if self._genres is None:
            response = await self._request("GET", f"{self.api_url}/api/genres")
            response.raise_for_status()
            self._genres = [
                (str(item["id"]), str(item["name"])) for item in response.json()
            ]
        filters = [
            SourceFilter("status", "Status", "select", [
                ("", "ALL"), ("ONGOING", "Ongoing"), ("COMPLETED", "Completed"),
                ("CANCELLED", "Canceled"), ("DROPPED", "Dropped"),
                ("COMING_SOON", "Coming Soon"), ("MASS_RELEASED", "Mass Released"),
            ], ""),
            SourceFilter("type", "Type", "select", [
                ("", "ALL"), ("MANGA", "Manga"), ("MANHUA", "Manhua"),
                ("MANHWA", "Manhwa"), ("RUSSIAN", "Russian"), ("SPANISH", "Spanish"),
            ], ""),
            SourceFilter("sort", "Sort", "select", [
                ("lastChapterAddedAt", "Latest Update"), ("totalViews", "Popularity"),
                ("createdAt", "Date Added"), ("chaptersCount", "Chapter Count"),
                ("postTitle", "A-Z"),
            ], "lastChapterAddedAt"),
            SourceFilter("direction", "Sort direction", "select", [
                ("desc", "Descending"), ("asc", "Ascending"),
            ], "desc"),
        ]
        if self._genres:
            filters.append(SourceFilter("genres", "Genres", "multi_select", self._genres, []))
        return filters

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        values = filters or {}
        return await self._query(page, query.strip(), {
            "seriesStatus": str(values.get("status", "")),
            "seriesType": str(values.get("type", "")),
            "orderBy": str(values.get("sort", "lastChapterAddedAt")),
            "orderDirection": str(values.get("direction", "desc")),
            **(
                {"genreIds": ",".join(str(value) for value in values["genres"])}
                if isinstance(values.get("genres"), list) and values["genres"] else {}
            ),
        })

    async def browse(self, kind: str, page: int = 1):
        order = {"popular": "totalViews", "latest": "lastChapterAddedAt"}.get(kind)
        if order is None:
            return {"items": [], "has_more": False}
        return await self._query(page, "", {"orderBy": order})

    async def _query(self, page: int, query: str, filters: dict[str, str]):
        base_params = {
            "perPage": str(self.per_page),
            "searchTerm": query,
            **filters,
        }
        key = tuple(sorted((name, value) for name, value in base_params.items() if value))
        actual_page = 1 if page == 1 else self._next_pages.get(key, page)
        if page == 1:
            self._next_pages.pop(key, None)
        while True:
            response = await self._request(
                "GET", f"{self.api_url}/api/query",
                params={"page": str(actual_page), **base_params},
            )
            response.raise_for_status()
            payload = response.json()
            posts = [item for item in payload.get("posts", []) if not self._is_novel(item)]
            has_more = int(payload.get("totalCount", 0)) > actual_page * self.per_page
            if posts or not has_more:
                break
            actual_page += 1
        if has_more:
            self._next_pages[key] = actual_page + 1
        else:
            self._next_pages.pop(key, None)
        return {"items": [self._manga(item) for item in posts], "has_more": has_more}

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        slug, _, post_id = str(series_id).rpartition("#")
        if self.use_chapters_api:
            response = await self._request(
                "GET", f"{self.api_url}/api/chapters", params={"postId": post_id},
            )
            response.raise_for_status()
            post = response.json().get("post") or {}
        else:
            response = await self._request(
                "GET", f"{self.api_url}/api/post", params={"postSlug": slug},
            )
            response.raise_for_status()
            post = response.json().get("post") or {}
            if self._is_novel(post):
                raise ValueError("Las novelas no son compatibles")
            await self._update_views(post_id=post.get("id"))
            slug = str(post.get("slug") or slug)
        result: list[SourceChapter] = []
        for chapter in post.get("chapters", []):
            accessible = bool(chapter.get("isAccessible"))
            locked = bool(chapter.get("isLocked") or chapter.get("isTimeLocked"))
            if not accessible and not (self.show_locked_chapters and locked):
                continue
            number = chapter.get("number")
            chapter_slug = str(chapter.get("slug", ""))
            series_slug = str((chapter.get("mangaPost") or {}).get("slug") or slug)
            title = f"{'🔒 ' if not accessible else ''}Chapter {number}"
            if str(chapter.get("title") or "").strip():
                title += f" - {str(chapter['title']).strip()}"
            creator = chapter.get("createdBy") or {}
            result.append(SourceChapter(
                source_id=f"/series/{series_slug}/{chapter_slug}#{chapter['id']}",
                title=title,
                series_id=str(series_id),
                source_name=self.name,
                number=float(number) if number is not None else None,
                language=self.language,
                scanlator=str(creator.get("name") or ""),
                uploaded_at=self._instant(str(chapter.get("createdAt", ""))),
            ))
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        _, _, raw_id = str(chapter_id).rpartition("#")
        if not raw_id:
            raise ValueError("Actualiza la lista de capítulos")
        response = await self._request(
            "GET", f"{self.api_url}/api/chapter", params={"chapterId": raw_id},
        )
        response.raise_for_status()
        data = response.json().get("chapter") or {}
        errors = (
            ("isShortLinkLocked", "Capítulo bloqueado por enlace corto"),
            ("isLockedByCoins", "Capítulo bloqueado: requiere monedas"),
            ("isPermanentlyLocked", "Capítulo bloqueado permanentemente"),
        )
        if message := next((message for key, message in errors if data.get(key)), None):
            raise ValueError(message)
        await self._update_views(chapter_id=raw_id)
        images = list(data.get("images", []))
        if self.sort_pages_by_filename:
            images.sort(key=lambda page: int(match.group()) if (match := re.search(r"\d+", page["url"].rsplit("/", 1)[-1])) else 10**9)
        else:
            images.sort(key=lambda page: page.get("order") if page.get("order") is not None else 10**9)
        return [
            SourcePage(
                source_id=str(page["url"]).replace(" ", "%20"),
                chapter_id=str(chapter_id),
                index=index,
                filename=str(page["url"]).rsplit("/", 1)[-1].split("?", 1)[0] or f"{index}.jpg",
                source_name=self.name,
            )
            for index, page in enumerate(images, 1)
        ]

    async def _update_views(self, post_id=None, chapter_id=None) -> None:
        if post_id is None and chapter_id is None:
            return
        try:
            await self._request(
                "POST", f"{self.api_url}/api/analytics/updateViews",
                json={"postId": post_id, "chapterId": chapter_id},
            )
        except Exception:
            pass

    def _manga(self, post: dict) -> SourceSeries:
        kind = str(post.get("seriesType") or "")
        tags = ([kind.title()] if kind in {"MANGA", "MANHUA", "MANHWA"} else []) + [
            str(item["name"]) for item in post.get("genres", []) if item.get("name")
        ]
        tags = list(dict.fromkeys(tags))
        description = self._html_text(str(post.get("postContent") or ""))
        if post.get("alternativeTitles"):
            description = f"{description}\n\nAlternative Names: {post['alternativeTitles']}".strip()
        slug = str(post["slug"])
        return SourceSeries(
            source_id=f"{slug}#{post['id']}",
            title=str(post.get("postTitle") or "Sin título"),
            source_name=self.name,
            cover_url=post.get("featuredImage"),
            description=description or None,
            author=str(post.get("author") or "").strip() or None,
            artist=str(post.get("artist") or "").strip() or None,
            status={
                "ONGOING": "ongoing", "COMING_SOON": "ongoing", "MASS_RELEASED": "ongoing",
                "COMPLETED": "completed", "CANCELLED": "cancelled", "DROPPED": "cancelled",
            }.get(post.get("seriesStatus")),
            content_tags=tuple(tags),
            metadata={"id": str(post["id"]), "slug": slug},
            web_url=f"{self.base_url}/series/{slug}",
        )

    @staticmethod
    def _is_novel(post: dict) -> bool:
        return bool(post.get("isNovel")) or str(post.get("seriesType", "")).casefold() == "novel"

    @staticmethod
    def _html_text(value: str) -> str:
        return _parse_html(value.replace("\n", "<br>")).text().strip()

    @staticmethod
    def _instant(value: str) -> str | None:
        from datetime import datetime
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
        except ValueError:
            return None
