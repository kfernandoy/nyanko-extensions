try:
    from .base import (
        FuenteBaseSource, _Node, _TreeParser
    )
except ImportError:
    pass

class FuenteBaseSource:
    pass


class OnepiecefansSource(FuenteBaseSource):
    """Cada serie es un fansub; capitulos e imagenes salen de server.php."""

    supports_latest = False
    chapter_prefix = "Chapter"

    @property
    def thumbnail_url(self) -> str:
        return f"{self.base_url}/images/luffy.png"

    def get_preferences(self) -> list[SourcePreference]:
        return [
            SourcePreference(
                id="pref_thumbnail_url",
                name="Thumbnail URL",
                type="text",
                default=self.thumbnail_url,
            )
        ]

    def get_filters(self) -> list[SourceFilter]:
        return []

    async def browse(self, kind: str, page: int = 1):
        # El sitio no publica recientes y el catalogo entero cabe en una respuesta.
        if kind != "popular":
            return {"items": [], "has_more": False}
        return {"items": await self._fansubs(), "has_more": False}

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        # El Kotlin reusa la peticion de populares y descarta la consulta.
        return {"items": await self._fansubs(), "has_more": False}

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        if isinstance(series, SourceSeries):
            return series
        return SourceSeries(
            source_id=str(series),
            title=str(series),
            source_name=self.name,
            cover_url=self.thumbnail_url,
            web_url=f"{self.base_url}/manga/{self.language}/{series}",
        )

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        folder = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request(
            "GET",
            f"{self.base_url}/server.php",
            params={"lang": self.language, "folderName": folder},
        )
        response.raise_for_status()
        result: list[SourceChapter] = []
        for number in response.json() or []:
            found = _ONEPIECEFANS_NUMBER.search(str(number))
            result.append(
                SourceChapter(
                    source_id=f"{folder}/{number}",
                    title=f"{self.chapter_prefix} {number}",
                    series_id=folder,
                    source_name=self.name,
                    number=float(found.group()) if found else None,
                    language=self.language,
                )
            )
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else str(chapter)
        folder, _, number = chapter_id.partition("/")
        response = await self._request(
            "GET",
            f"{self.base_url}/server.php",
            params={"lang": self.language, "folderName": folder, "chapter": number},
        )
        response.raise_for_status()
        return [
            SourcePage(
                source_id=f"{self.base_url}/mangafiles/{self.language}/{folder}/{number}/{filename}",
                chapter_id=chapter_id,
                index=index,
                filename=str(filename),
                source_name=self.name,
            )
            for index, filename in enumerate(response.json() or [])
        ]

    async def _fansubs(self) -> list[SourceSeries]:
        response = await self._request("GET", f"{self.base_url}/fansubs-config.json")
        response.raise_for_status()
        payload = response.json() or {}
        return [
            SourceSeries(
                source_id=str(item.get("path") or ""),
                title=f"One Piece ({item.get('title') or ''})",
                source_name=self.name,
                cover_url=self.thumbnail_url,
                web_url=f"{self.base_url}/manga/{self.language}/{item.get('path') or ''}",
            )
            for item in payload.get(self.language) or []
            if isinstance(item, dict)
        ]




SOURCE = OnepiecefansSource
