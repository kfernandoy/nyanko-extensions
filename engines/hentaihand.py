"""Implementación JSON común de HentaiHand."""

try:
    from .base import FuenteBaseSource, SourceChapter, SourcePage, SourceSeries
except ImportError:
    pass


class HentaiHandSource(FuenteBaseSource):
    language_ids: list[int] = []

    def _language_params(self) -> dict[str, int]:
        return {
            f"languages[{-index - 1}]": value
            for index, value in enumerate(self.language_ids)
        }

    def _series(self, rows: list[dict]) -> list[SourceSeries]:
        # La API ya devuelve la miniatura en cada fila; sin leerla el catalogo salia
        # entero sin portadas (0 de 18) aunque el dato estuviera ahi.
        return [
            SourceSeries(
                source_id=f"{self.base_url}/en/comic/{row['slug']}",
                title=row["title"],
                source_name=self.name,
                cover_url=row.get("thumb_url") or row.get("image_url") or None,
                web_url=f"{self.base_url}/en/comic/{row['slug']}",
            )
            for row in rows
            if row.get("slug") and row.get("title")
        ]

    @staticmethod
    def _nombres(filas: object) -> list[str]:
        return [
            str(fila["name"]).strip()
            for fila in (filas if isinstance(filas, list) else [])
            if isinstance(fila, dict) and str(fila.get("name") or "").strip()
        ]

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        # El engine no implementaba `details`, asi que caia en el de Madara -que busca
        # markup HTML- y devolvia la ficha entera vacia sobre una fuente que es JSON.
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        slug = series_id.rstrip("/").rsplit("/", 1)[-1]
        response = await self._request("GET", f"{self.base_url}/api/comics/{slug}")
        response.raise_for_status()
        data = response.json()

        etiquetas = self._nombres(data.get("tags"))
        for clave in ("parodies", "characters", "relationships"):
            etiquetas.extend(self._nombres(data.get(clave)))
        categoria = data.get("category")
        if isinstance(categoria, dict) and str(categoria.get("name") or "").strip():
            etiquetas.insert(0, str(categoria["name"]).strip())

        return SourceSeries(
            source_id=series_id,
            title=str(data.get("title") or "").strip() or (
                series.title if isinstance(series, SourceSeries) else slug
            ),
            source_name=self.name,
            cover_url=data.get("image_url") or data.get("thumb_url") or None,
            description=(str(data.get("description") or "").strip() or None),
            author=", ".join(self._nombres(data.get("authors"))) or None,
            artist=", ".join(self._nombres(data.get("artists"))) or None,
            # Estos comics son one-shots: la API no publica estado de serializacion.
            status=None,
            content_tags=tuple(dict.fromkeys(etiquetas)),
            web_url=f"{self.base_url}/en/comic/{slug}",
        )

    async def _catalog(self, page: int, sort: str, query: str = "") -> list[SourceSeries]:
        params = {
            "page": max(page, 1),
            "sort": sort,
            "order": "desc",
            "duration": "all",
            **self._language_params(),
        }
        if query:
            params["q"] = query
        response = await self._request("GET", f"{self.base_url}/api/comics", params=params)
        response.raise_for_status()
        return self._series(response.json().get("data", []))

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        return (await self._catalog(1, "uploaded_at", query.strip()))[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"}:
            return []
        return await self._catalog(page, "popularity" if kind == "popular" else "uploaded_at")

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        slug = series_id.rstrip("/").rsplit("/", 1)[-1]
        response = await self._request("GET", f"{self.base_url}/api/comics/{slug}")
        response.raise_for_status()
        data = response.json()
        chapter_slug = data.get("slug") or slug
        return [
            SourceChapter(
                source_id=f"{self.base_url}/api/comics/{chapter_slug}/images",
                title="Chapter",
                series_id=series_id,
                source_name=self.name,
                number=1.0,
                uploaded_at=data.get("updated_at"),
            )
        ]

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        response = await self._request("GET", chapter_id)
        response.raise_for_status()
        return [
            SourcePage(
                source_id=row["source_url"],
                chapter_id=chapter_id,
                index=int(row.get("page", index)),
                filename=row["source_url"].rsplit("/", 1)[-1].split("?", 1)[0],
                source_name=self.name,
            )
            for index, row in enumerate(response.json().get("images", []), 1)
            if row.get("source_url")
        ]
