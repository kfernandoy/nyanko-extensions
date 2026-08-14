try:
    from .base import (
        FuenteBaseSource, _Node, _TreeParser
    )
except ImportError:
    pass

class FuenteBaseSource:
    pass


class MantaSource(FuenteBaseSource):
    """base_url incluye el idioma; la API cuelga del host a secas."""

    supports_latest = False

    def __init__(self, fetcher: SourceFetcher | None = None) -> None:
        super().__init__(fetcher)
        headers = {
            "User-Agent": "Nyanko/0.2.4",
            "Origin": self.api_url,
            "Accept-Language": self.language,
        }
        self.capabilities = SourceCapabilities(
            search=True,
            browse=True,
            headers=headers,
            requests_per_minute=self.requests_per_minute,
            content_warning=self.content_warning,
            requires_auth=self.requires_auth,
        )
        self.image_headers = dict(headers)

    @property
    def api_url(self) -> str:
        parsed = urlparse(self.base_url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def get_filters(self) -> list[SourceFilter]:
        options = _MANTA_CATEGORIES.get(self.language, _MANTA_CATEGORIES["en"])
        return [
            SourceFilter(
                "category",
                "Categoría" if self.language == "es" else "Category",
                "select",
                list(options),
                options[0][0],
            )
        ]

    async def browse(self, kind: str, page: int = 1):
        # No hay recientes: populares reusa el listado "New" de la API.
        if kind != "popular":
            return {"items": [], "has_more": False}
        return await self._series([("cat", "New"), ("lang", self.language)])

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        params: list[tuple[str, str]] = [("lang", self.language)]
        if query.strip():
            params.append(("q", query.strip()))
        else:
            selected = str((filters or {}).get("category") or "") or "tagId=288"
            key, _, value = selected.partition("=")
            params.append((key, value))
        return await self._series(params)

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        payload = await self._get(f"/front/v1/series/{series_id}")
        holder = (payload.get("data") or {})
        data = holder.get("data") or {}
        creators = [item for item in data.get("creators") or [] if isinstance(item, dict)]
        artists = [str(item.get("name") or "") for item in creators if item.get("role") == "Illustration"]
        authors = [str(item.get("name") or "") for item in creators if item.get("role") != "Illustration"]
        if not authors:
            authors = [str(item.get("name") or "") for item in creators]
        description = data.get("description") or {}
        known = series if isinstance(series, SourceSeries) else None
        return SourceSeries(
            source_id=series_id,
            title=known.title if known else series_id,
            source_name=self.name,
            cover_url=(known.cover_url if known else None) or self._cover(holder.get("image")),
            description="\n\n".join(
                value
                for value in (description.get("short"), description.get("long"))
                if value
            ) or None,
            author=", ".join(authors) or None,
            artist=", ".join(artists) or None,
            status="completed" if data.get("isCompleted") is True else "ongoing",
            content_tags=tuple(
                self._localized((tag or {}).get("name"))
                for tag in data.get("tags") or []
                if isinstance(tag, dict)
            ),
            web_url=f"{self.base_url}/series/{series_id}",
        )

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        payload = await self._get(f"/front/v1/series/{series_id}")
        episodes = ((payload.get("data") or {}).get("episodes")) or []
        result: list[SourceChapter] = []
        for episode in episodes:
            if not isinstance(episode, dict) or self._locked(episode.get("lockData")):
                continue
            result.append(
                SourceChapter(
                    source_id=str(episode.get("id")),
                    title=self._episode_title(episode),
                    series_id=series_id,
                    source_name=self.name,
                    number=float(episode.get("ord") or 0),
                    language=self.language,
                    uploaded_at=self._timestamp(episode.get("openAt")) or self._timestamp(episode.get("createdAt")),
                )
            )
        return result[::-1]

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else str(chapter)
        payload = await self._get(f"/front/v1/episodes/{chapter_id}")
        images = ((payload.get("data") or {}).get("cutImages")) or []
        urls = [
            str(image.get("downloadUrl") or "")
            for image in images
            if isinstance(image, dict)
        ]
        return [
            SourcePage(
                source_id=value,
                chapter_id=chapter_id,
                index=index,
                filename=urlparse(value).path.rsplit("/", 1)[-1] or f"{index}.jpg",
                source_name=self.name,
            )
            for index, value in enumerate(value for value in urls if value)
        ]

    async def _series(self, params: list[tuple[str, str]]) -> dict:
        response = await self._request(
            "GET", f"{self.api_url}/manta/v1/search/series", params=params,
        )
        response.raise_for_status()
        payload = response.json() or {}
        return {
            "items": [
                SourceSeries(
                    source_id=str(item.get("id")),
                    title=self._localized(((item.get("data") or {}).get("title"))),
                    source_name=self.name,
                    cover_url=self._cover(item.get("image")) or None,
                    web_url=f"{self.base_url}/series/{item.get('id')}",
                )
                for item in payload.get("data") or []
                if isinstance(item, dict)
            ],
            "has_more": False,
        }

    async def _get(self, path: str) -> dict:
        response = await self._request(
            "GET", f"{self.api_url}{path}", params={"lang": self.language},
        )
        response.raise_for_status()
        return response.json() or {}

    def _localized(self, value: Any) -> str:
        if not isinstance(value, dict):
            return str(value or "")
        english, spanish = value.get("en"), value.get("es")
        if self.language == "es":
            return str(spanish or english or "")
        return str(english or spanish or "")

    def _episode_title(self, episode: dict) -> str:
        title = (episode.get("data") or {}).get("title")
        if title:
            return str(title)
        word = "Episodio" if self.language == "es" else "Episode"
        return f"{word} {episode.get('ord')}"

    @staticmethod
    def _cover(value: Any) -> str:
        if not isinstance(value, dict):
            return ""
        for key in _MANTA_COVERS:
            image = value.get(key)
            if isinstance(image, dict) and image.get("downloadUrl"):
                return str(image["downloadUrl"])
        return ""

    @staticmethod
    def _locked(value: Any) -> bool:
        if not isinstance(value, dict):
            return False
        state = value.get("state")
        return state is not None and state not in _MANTA_UNLOCKED

    @staticmethod
    def _timestamp(value: Any) -> str | None:
        from datetime import datetime

        if not value:
            return None
        text = str(value).split(".")[0].split("+")[0].split("Z")[0]
        try:
            return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S").isoformat()
        except ValueError:
            return None




SOURCE = MantaSource
