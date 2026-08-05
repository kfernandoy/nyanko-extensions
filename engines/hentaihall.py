"""Adaptador de la API de HentaiHall."""

from datetime import datetime, timezone
from urllib.parse import urlparse

try:
    from .madara import (
        MadaraSource,
        SourceChapter,
        SourceFilter,
        SourcePage,
        SourceSeries,
    )
except ImportError:
    pass


class HentaiHallSource(MadaraSource):
    api_url = "https://hentaihallbackend-production.up.railway.app"
    genres: tuple[str, ...] = ()

    def __init__(self, fetcher=None) -> None:
        super().__init__(fetcher)
        self.api_headers = {
            "Accept": "application/json, text/plain, */*",
            "Origin": self.base_url,
            "Referer": f"{self.base_url}/",
        }
        self.image_headers = {
            "Accept": "*/*",
            "Origin": self.base_url,
            "Referer": f"{self.base_url}/",
        }
        self.capabilities.headers.update(self.api_headers)

    def get_filters(self) -> list[SourceFilter]:
        return [
            SourceFilter("search_by", "Buscar por", "select", [
                ("nombre", "Nombre"), ("autores", "Autores"),
            ], "nombre"),
            SourceFilter("sort", "Ordenar por", "sort", [
                ("alfabetico", "Alfabético"), ("creacion", "Creación"),
                ("seguir", "Popularidad"),
            ], "seguir"),
            SourceFilter("direction", "Dirección", "select", [
                ("desc", "Descendente"), ("asc", "Ascendente"),
            ], "desc"),
            SourceFilter("genres", "Géneros", "multi_select", [
                (genre, genre) for genre in self.genres
            ], []),
        ]

    async def browse(self, kind: str, page: int = 1):
        order = {"popular": "seguir", "latest": "creacion"}.get(kind)
        if order is None:
            return {"items": [], "has_more": False}
        return await self._library(page, "", "nombre", order, "desc", [])

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        values = filters or {}
        genres = values.get("genres", [])
        return await self._library(
            page,
            query,
            str(values.get("search_by", "nombre")),
            str(values.get("sort", "seguir")),
            str(values.get("direction", "desc")),
            genres if isinstance(genres, list) else [],
        )

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request(
            "GET", f"{self.api_url}/manhwa/see/{series_id}", headers=self.api_headers,
        )
        response.raise_for_status()
        return self._details(response.json())

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request(
            "GET", f"{self.api_url}/manhwa/see/{series_id}", headers=self.api_headers,
        )
        response.raise_for_status()
        item = response.json()
        return [SourceChapter(
            source_id=str(item["_id"]),
            title="Chapter",
            series_id=series_id,
            source_name=self.name,
            language=self.language,
            uploaded_at=self._date(item.get("creacion")),
        )]

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else str(chapter)
        response = await self._request(
            "GET", f"{self.api_url}/manhwa/chapter/{chapter_id}", headers=self.api_headers,
        )
        response.raise_for_status()
        return [
            SourcePage(
                source_id=url,
                chapter_id=chapter_id,
                index=index,
                filename=urlparse(url).path.rsplit("/", 1)[-1] or f"{index}.jpg",
                source_name=self.name,
            )
            for index, url in enumerate(
                (str(url).strip() for url in response.json().get("chapter", [])),
                1,
            )
            if url
        ]

    async def _library(
        self,
        page: int,
        query: str,
        search_by: str,
        order: str,
        direction: str,
        genres: list,
    ):
        response = await self._request(
            "GET", f"{self.api_url}/manhwa/library",
            params={
                "buscar": query,
                "quebusca": search_by,
                "order_item": order,
                "order_dir": direction,
                "page": str(page - 1),
                "generes": "_".join(str(genre) for genre in genres),
            },
            headers=self.api_headers,
        )
        response.raise_for_status()
        payload = response.json()
        return {
            "items": [self._manga(item) for item in payload.get("data", [])],
            "has_more": bool(payload.get("next")),
        }

    def _manga(self, item: dict) -> SourceSeries:
        source_id = str(item["_id"])
        return SourceSeries(
            source_id=source_id,
            title=str(item["nombre"]),
            source_name=self.name,
            cover_url=item.get("imagen"),
            web_url=f"{self.base_url}/content/{source_id}",
        )

    def _details(self, item: dict) -> SourceSeries:
        authors = ", ".join(str(value) for value in item.get("autores", []))
        description: list[str] = []
        if item.get("tipo"):
            description.append(f"Tipo: {str(item['tipo']).capitalize()}")
        if item.get("lenguaje"):
            language = "Español" if item["lenguaje"] == "esp" else str(item["lenguaje"])
            description.append(f"Lenguaje: {language}")
        if item.get("name_grupo"):
            description.append(f"Grupo: {item['name_grupo']}")
        source_id = str(item["_id"])
        return SourceSeries(
            source_id=source_id,
            title=str(item["nombre"]),
            source_name=self.name,
            cover_url=item.get("imagen"),
            description="\n".join(description) or None,
            author=authors or None,
            artist=authors or None,
            status="completed",
            content_tags=tuple(str(value) for value in item.get("tags", [])),
            web_url=f"{self.base_url}/content/{source_id}",
        )

    @staticmethod
    def _date(value) -> str | None:
        try:
            return datetime.strptime(str(value), "%Y-%m-%dT%H:%M:%S.%fZ").replace(
                tzinfo=timezone.utc,
            ).isoformat()
        except (TypeError, ValueError):
            return None
