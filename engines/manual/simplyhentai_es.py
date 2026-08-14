try:
    from .base import FuenteBaseSource, _Node, _TreeParser
except ImportError:
    pass

class FuenteBaseSource:
    pass


class SimplyhentaiSource(FuenteBaseSource):
    """Cada album es una sola serie con un unico capitulo de todas las paginas."""

    @property
    def language_tag(self) -> str:
        return _SIMPLYHENTAI_LANGS.get(self.language, self.language)

    def get_preferences(self) -> list[SourcePreference]:
        return [
            SourcePreference(
                id="blacklist",
                name="Blacklist",
                type="text",
                default="",
            )
        ]

    def get_filters(self) -> list[SourceFilter]:
        return [
            SourceFilter("sort", "Sort by", "select", [
                ("", "Relevance"), ("upload-date", "Upload Date"), ("popularity", "Popularity"),
            ], ""),
            SourceFilter("series", "Series", "text", default=""),
            SourceFilter("tags", "Tags", "text", default=""),
            SourceFilter("artists", "Artists", "text", default=""),
            SourceFilter("translators", "Translators", "text", default=""),
            SourceFilter("characters", "Characters", "text", default=""),
        ]

    async def browse(self, kind: str, page: int = 1):
        if kind not in {"popular", "latest"}:
            return {"items": [], "has_more": False}
        params = [("type", "language"), ("page", str(page))]
        if kind == "latest":
            params.append(("sort", "newest"))
        payload = await self._get(f"/tag/{self.language_tag}", params)
        albums = (payload.get("data") or {}).get("albums") or []
        return {
            "items": [self._series(item) for item in albums if isinstance(item, dict)],
            "has_more": self._has_next(payload),
        }

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        values = filters or {}
        params: list[tuple[str, str]] = [
            ("query", query),
            ("page", str(page)),
            # La lista negra vive en las preferencias, que hoy no vuelven a la fuente.
            ("blacklist", ""),
            ("filter[language][0]", self.language_tag[:1].upper() + self.language_tag[1:]),
        ]
        if str(values.get("sort") or ""):
            params.append(("sort", str(values["sort"])))
        if str(values.get("series") or "").strip():
            params.append(("filter[series_title][0]", str(values["series"]).strip()))
        for key, parameter in (
            ("tags", "filter[tags]"), ("artists", "filter[artists]"),
            ("translators", "filter[translators]"), ("characters", "filter[characters]"),
        ):
            raw = str(values.get(key) or "")
            if not raw.strip():
                continue
            params.extend(
                (f"{parameter}[{index}]", part.strip())
                for index, part in enumerate(raw.split(","))
            )
        payload = await self._get("/search/complex", params)
        return {
            "items": [
                self._series(item["object"])
                for item in payload.get("data") or []
                if isinstance(item, dict) and isinstance(item.get("object"), dict)
            ],
            "has_more": self._has_next(payload),
        }

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        album = await self._album(series_id)
        description = str(album.get("description") or "")
        parts = [f"{description}\n\n"] if description else []
        parts.append(f"Series: {(album.get('series') or {}).get('title', '')}\n")
        parts.append(
            "Characters: "
            + ", ".join(str(item.get("title") or "") for item in album.get("characters") or [])
        )
        artists = ", ".join(str(item.get("title") or "") for item in album.get("artists") or [])
        return SourceSeries(
            source_id=self._path(album),
            title=str(album.get("title") or ""),
            source_name=self.name,
            cover_url=((album.get("preview") or {}).get("sizes") or {}).get("thumb") or None,
            description="".join(parts),
            author=artists or None,
            artist=artists or None,
            content_tags=tuple(
                str(item.get("title") or "") for item in album.get("tags") or []
            ),
            web_url=urljoin(f"{self.base_url}/", self._path(album)),
        )

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        album = await self._album(series_id)
        path = self._path(album)
        return [
            SourceChapter(
                source_id=f"{path}/all-pages",
                title="Chapter",
                series_id=path,
                source_name=self.name,
                number=1.0,
                language=self.language,
                scanlator=", ".join(
                    str(item.get("title") or "") for item in album.get("translators") or []
                ),
                uploaded_at=self._date(album.get("created_at")),
            )
        ]

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else str(chapter)
        payload = await self._get(f"/manga/{self._album_slug(chapter_id)}/pages", [])
        return [
            SourcePage(
                source_id=str((image.get("sizes") or {}).get("full") or ""),
                chapter_id=chapter_id,
                index=int(image.get("page_num") or index),
                filename=urlparse(
                    str((image.get("sizes") or {}).get("full") or "")
                ).path.rsplit("/", 1)[-1] or f"{index}.jpg",
                source_name=self.name,
            )
            for index, image in enumerate((payload.get("data") or {}).get("pages") or [])
            if isinstance(image, dict) and (image.get("sizes") or {}).get("full")
        ]

    async def _album(self, series_id: str) -> dict:
        payload = await self._get(f"/manga/{self._album_slug(series_id)}", [])
        return payload.get("data") or {}

    async def _get(self, path: str, params: list[tuple[str, str]]) -> dict:
        response = await self._request("GET", f"{_SIMPLYHENTAI_API}{path}", params=params)
        response.raise_for_status()
        return response.json() or {}

    def _series(self, item: dict) -> SourceSeries:
        path = f"{(item.get('series') or {}).get('slug', '')}/{item.get('slug') or ''}"
        return SourceSeries(
            source_id=path,
            title=str(item.get("title") or ""),
            source_name=self.name,
            cover_url=((item.get("preview") or {}).get("sizes") or {}).get("thumb") or None,
            web_url=urljoin(f"{self.base_url}/", path),
        )

    @staticmethod
    def _path(album: dict) -> str:
        return f"{(album.get('series') or {}).get('slug', '')}/{album.get('slug') or ''}"

    @staticmethod
    def _album_slug(path: str) -> str:
        # El id es "<serie>/<album>"; el Kotlin toma el segundo tramo de la ruta.
        parts = [part for part in path.split("/") if part]
        return parts[1] if len(parts) > 1 else (parts[0] if parts else "")

    @staticmethod
    def _has_next(payload: dict) -> bool:
        return (payload.get("pagination") or {}).get("next") is not None

    @staticmethod
    def _date(value: Any) -> str | None:
        from datetime import datetime

        if not value:
            return None
        try:
            return datetime.strptime(str(value), "%Y-%m-%dT%H:%M:%S.%f").isoformat()
        except ValueError:
            return None


class GeneratedSimplyHentaiSource(SimplyHentaiSource):
    name = 'simplyhentai_es'
    display_name = 'Simply Hentai'
    base_url = 'https://www.simply-hentai.com'
    language = 'es'
    requests_per_minute = 60
    content_warning = 'nsfw'
    image_headers = {'Referer': 'https://www.simply-hentai.com/'}


SOURCE = SimplyhentaiSource
