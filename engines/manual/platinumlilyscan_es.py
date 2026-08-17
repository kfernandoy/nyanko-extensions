try:
    from .madara import (
        MadaraSource, _Node, _TreeParser
    )
except ImportError:
    pass

class MadaraSource:
    pass


_PLATINUM_GENRES = (
    "Acción", "Apocalíptico", "Aventura", "Ciencia Ficción", "Cocina", "Comedia", "Drama",
    "Ecchi", "Escolar", "Fantasía", "Histórico", "Horror", "Isekai", "Magia", "Mecha",
    "Misterio", "Música", "Psicológico", "Romance", "Slice of Life", "Sobrenatural",
    "Supervivencia", "Tragedia", "Vampiros", "Yuri",
)
_PLATINUM_STATUS = {
    "ONGOING": "ongoing",
    "COMPLETED": "completed",
    "HIATUS": "hiatus",
}


class PlatinumlilyscanSource(MadaraSource):
    """El catalogo entero llega en /api/series y se ordena y filtra en el cliente."""

    def get_filters(self) -> list[SourceFilter]:
        return [
            SourceFilter("type", "Tipo", "select", [
                ("", "Todos"), ("MANGA", "Manga"), ("MANHWA", "Manhwa"), ("MANHUA", "Manhua"),
                ("DOUJINSHI", "Doujinshi"), ("ONE_SHOT", "One-Shot"),
            ], ""),
            SourceFilter("status", "Estado", "select", [
                ("", "Todos"), ("ONGOING", "Publicándose"),
                ("COMPLETED", "Finalizado"), ("HIATUS", "Hiatus"),
            ], ""),
            SourceFilter("contentRating", "Clasificación de contenido", "select", [
                ("", "Todos"), ("SAFE", "Seguro"), ("SUGGESTIVE", "Sugestivo"), ("NSFW", "NSFW"),
            ], ""),
            SourceFilter("genre", "Género", "select", [("", "Todos")] + [
                (value, value) for value in _PLATINUM_GENRES
            ], ""),
        ]

    async def browse(self, kind: str, page: int = 1):
        if kind not in {"popular", "latest"}:
            return {"items": [], "has_more": False}
        entries = await self._catalog()
        if kind == "popular":
            entries.sort(key=lambda item: int((item.get("_count") or {}).get("bookmarks") or 0), reverse=True)
        else:
            entries.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
        return {"items": [self._series(item) for item in entries], "has_more": False}

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        values = filters or {}
        needle = query.strip().casefold()
        genre = str(values.get("genre") or "")
        entries = [
            item
            for item in await self._catalog()
            if (not needle or self._matches_query(item, needle))
            and self._matches(item, values, genre)
        ]
        entries.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
        return {"items": [self._series(item) for item in entries], "has_more": False}

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        return self._series(await self._series_payload(series_id))

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        payload = await self._series_payload(series_id)
        slug = str(payload.get("slug") or series_id)
        result: list[SourceChapter] = []
        for item in payload.get("chapters") or []:
            if not isinstance(item, dict) or not str(item.get("id") or ""):
                continue
            number = float(item.get("number") or -1)
            label = str(number)
            label = label[:-2] if label.endswith(".0") else label
            title = str(item.get("title") or "").strip()
            result.append(
                SourceChapter(
                    source_id=f"{slug}#{item['id']}",
                    title=f"Capítulo {label}" + (f" - {title}" if title else ""),
                    series_id=series_id,
                    source_name=self.name,
                    number=number,
                    language=self.language,
                    uploaded_at=self._date(item.get("publishedAt")),
                )
            )
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else str(chapter)
        slug, _, identifier = chapter_id.partition("#")
        payload = await self._series_payload(slug)
        found = next(
            (
                item
                for item in payload.get("chapters") or []
                if isinstance(item, dict) and str(item.get("id")) == identifier
            ),
            None,
        )
        if found is None:
            raise SourceNotFoundError("Capítulo no encontrado")
        return [
            SourcePage(
                source_id=f"{self.base_url}{image.get('imageUrl')}",
                chapter_id=chapter_id,
                index=index,
                filename=str(image.get("imageUrl") or "").rsplit("/", 1)[-1] or f"{index}.jpg",
                source_name=self.name,
            )
            for index, image in enumerate(found.get("pages") or [])
            if isinstance(image, dict) and image.get("imageUrl")
        ]

    async def _catalog(self) -> list[dict]:
        response = await self._request("GET", f"{self.base_url}/api/series")
        response.raise_for_status()
        return [item for item in response.json() or [] if isinstance(item, dict)]

    async def _series_payload(self, slug: str) -> dict:
        response = await self._request("GET", f"{self.base_url}/api/series/{slug}")
        response.raise_for_status()
        return response.json() or {}

    def _series(self, item: dict) -> SourceSeries:
        cover = item.get("coverUrl")
        return SourceSeries(
            source_id=str(item.get("slug") or ""),
            title=str(item.get("title") or ""),
            source_name=self.name,
            cover_url=f"{self.base_url}{cover}" if cover else None,
            description=str(item.get("description") or "") or None,
            author=str(item.get("author") or "") or None,
            artist=str(item.get("artist") or "") or None,
            status=_PLATINUM_STATUS.get(str(item.get("status") or "")),
            content_tags=tuple(self._genres(item)),
            web_url=f"{self.base_url}/series/{item.get('slug')}",
        )

    @staticmethod
    def _genres(item: dict) -> list[str]:
        return [
            str((entry.get("genre") or {}).get("name"))
            for entry in item.get("genres") or []
            if isinstance(entry, dict) and (entry.get("genre") or {}).get("name")
        ]

    @staticmethod
    def _matches_query(item: dict, needle: str) -> bool:
        return (
            needle in str(item.get("title") or "").casefold()
            or needle in str(item.get("altTitles") or "").casefold()
        )

    @classmethod
    def _matches(cls, item: dict, values: dict, genre: str) -> bool:
        for key in ("type", "status", "contentRating"):
            chosen = str(values.get(key) or "")
            if chosen and str(item.get(key) or "") != chosen:
                return False
        if genre and not any(
            name.casefold() == genre.casefold() for name in cls._genres(item)
        ):
            return False
        return True

    @staticmethod
    def _date(value: Any) -> str | None:
        from datetime import datetime

        if not value:
            return None
        try:
            return datetime.strptime(str(value), "%Y-%m-%dT%H:%M:%S.%fZ").replace(
                microsecond=0,
            ).isoformat()
        except ValueError:
            return None




SOURCE = PlatinumlilyscanSource
