"""Implementación común de MoonlightTL."""

from urllib.parse import urljoin

try:
    from .base import (
        FuenteBaseSource,
        SourceChapter,
        SourceFilter,
        SourcePage,
        SourceSeries,
        _first,
        _image_url,
        _parse_html,
    )
except ImportError:
    pass


class MoonlightTLSource(FuenteBaseSource):
    profile = "regular"
    requests_per_minute = 120

    def __init__(self, fetcher=None) -> None:
        super().__init__(fetcher)
        self._comics: list[dict] = []

    def get_filters(self) -> list[SourceFilter]:
        return [
            SourceFilter("sort", "Ordenar por", "select", [
                ("name", "Nombre"), ("views", "Vistas"),
                ("updated_at", "Actualizado"), ("created_at", "Creado"),
            ], "updated_at"),
            SourceFilter("ascending", "Ascendente", "checkbox", default=False),
            SourceFilter("status", "Estado", "select", [
                ("0", "Todos"), ("1", "En curso"), ("2", "Pausado"),
                ("3", "Abandonado"), ("4", "Completado"),
            ], "0"),
        ]

    @staticmethod
    def _rows(payload) -> list[dict]:
        if isinstance(payload, list):
            return [row for item in payload for row in MoonlightTLSource._rows(item)]
        if not isinstance(payload, dict):
            return []
        if "project" in payload:
            return MoonlightTLSource._rows(payload["project"])
        if payload.get("slug") and payload.get("name"):
            return [payload]
        return [
            row
            for key in ("diario", "semanal", "mensual")
            for row in MoonlightTLSource._rows(payload.get(key, []))
        ]

    def _series(self, payload) -> list[SourceSeries]:
        result: list[SourceSeries] = []
        seen: set[str] = set()
        for row in self._rows(payload):
            slug = str(row.get("slug", "")).strip()
            title = str(row.get("name", "")).strip()
            if slug and title and slug not in seen:
                seen.add(slug)
                result.append(
                    SourceSeries(
                        source_id=f"{self.base_url}/ver/{slug}",
                        title=title,
                        source_name=self.name,
                        cover_url=str(row.get("urlImg") or "") or None,
                        web_url=f"{self.base_url}/ver/{slug}",
                    )
                )
        return result

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        if query.strip() and len(query.strip()) < 2:
            raise ValueError("La búsqueda debe tener al menos 2 caracteres")
        if not self._comics:
            response = await self._request("GET", f"{self.base_url}/api/comics")
            response.raise_for_status()
            self._comics = self._rows(response.json().get("response", []))
        needle = query.strip().casefold()
        matches = [
            row
            for row in self._comics
            if not needle or needle in str(row.get("name", "")).casefold()
            or needle in str(row.get("alternativeName", "")).casefold()
        ]
        values = filters or {}
        status = int(values.get("status", 0) or 0)
        if status:
            matches = [row for row in matches if int(row.get("state_id") or 0) == status]
        sort = str(values.get("sort", "updated_at"))
        if sort == "views":
            matches.sort(key=lambda row: int((row.get("trending") or {}).get("visitas") or 0))
        else:
            key = {"name": "name", "updated_at": "actualizacionCap", "created_at": "created_at"}.get(sort, "actualizacionCap")
            matches.sort(key=lambda row: str(row.get(key) or ""))
        if not values.get("ascending", False):
            matches.reverse()
        start = (page - 1) * 15
        return {"items": self._series(matches[start:start + 15]), "has_more": len(matches) > start + 15}

    async def browse(self, kind: str, page: int = 1):
        if kind not in {"popular", "latest"} or page != 1:
            return {"items": [], "has_more": False}
        endpoint = "topSerie" if kind == "popular" else "lastUpdates"
        response = await self._request("GET", f"{self.base_url}/api/{endpoint}")
        response.raise_for_status()
        return {"items": self._series(response.json().get("response", [])), "has_more": False}

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        slug = series_id.rstrip("/").rsplit("/", 1)[-1]
        response = await self._request("GET", f"{self.base_url}/api/showProject/{slug}")
        response.raise_for_status()
        row = response.json().get("response", {})
        alternative = str(row.get("alternativeName") or "").strip()
        description = str(row.get("sinopsis") or "").strip()
        if alternative:
            description = f"{description}\n\nNombres alternativos: {alternative}".strip()
        nested_names = lambda values, key: ", ".join(
            str((item.get(key) or {}).get("name", "")).strip()
            for item in values or [] if str((item.get(key) or {}).get("name", "")).strip()
        )
        return SourceSeries(
            source_id=series_id,
            title=str(row.get("name") or slug),
            source_name=self.name,
            cover_url=str(row.get("urlImg") or "") or None,
            description=description or None,
            author=nested_names(row.get("autors"), "autor") or None,
            artist=nested_names(row.get("artists"), "artist") or None,
            status={1: "ongoing", 2: "hiatus", 3: "cancelled", 4: "completed"}.get(row.get("state_id")),
            content_tags=tuple(
                name for item in row.get("genders", [])
                if (name := str((item.get("gender") or {}).get("name", "")).strip())
            ),
            web_url=f"{self.base_url}/ver/{slug}",
        )

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        slug = series_id.rstrip("/").rsplit("/", 1)[-1]
        response = await self._request("GET", f"{self.base_url}/api/showProject/{slug}")
        response.raise_for_status()
        data = response.json().get("response", {})
        result: list[SourceChapter] = []
        for row in data.get("lastChapters", []):
            chapter_slug = str(row.get("slug", "")).strip()
            if not chapter_slug:
                continue
            number = row.get("num")
            title = f"Capítulo {number:g}" if isinstance(number, (int, float)) else "Capítulo"
            if row.get("name"):
                title += f" - {row['name']}"
            result.append(
                SourceChapter(
                    source_id=f"{self.base_url}/ver/{slug}/{chapter_slug}",
                    title=title,
                    series_id=series_id,
                    source_name=self.name,
                    number=float(number) if isinstance(number, (int, float)) else None,
                    language=self.language,
                    uploaded_at=row.get("created_at"),
                )
            )
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        response = await self._request("GET", chapter_id)
        response.raise_for_status()
        root = _parse_html(response.text)
        form = _first(root, lambda node: node.tag == "form" and node.attrs.get("method", "").lower() == "post")
        if form is not None and form.attrs.get("action"):
            data = {
                node.attrs["name"]: node.attrs.get("value", "")
                for node in form.descendants("input")
                if node.attrs.get("name")
            }
            response = await self._request(
                "POST",
                urljoin(str(response.url), form.attrs["action"]),
                data=data,
                headers={"Referer": str(response.url)},
            )
            response.raise_for_status()
            root = _parse_html(response.text)
        urls: list[str] = []
        for image in root.descendants("img"):
            if self.profile == "asteria":
                selected = bool(
                    image.has_class("block") and image.parent is not None
                    and image.parent.tag == "div" and image.parent.parent is not None
                    and image.parent.parent.tag == "main"
                )
            else:
                parent = image.parent
                selected = bool(
                    parent
                    and (
                        parent.tag == "main"
                        or any(node.tag == "main" and node.has_class("contenedor") for node in self._parents(image))
                    )
                )
            if selected and (url := _image_url(image, str(response.url))):
                urls.append(url)
        return [
            SourcePage(
                source_id=url,
                chapter_id=chapter_id,
                index=index,
                filename=url.rsplit("/", 1)[-1].split("?", 1)[0] or f"{index}.jpg",
                source_name=self.name,
            )
            for index, url in enumerate(urls, 1)
        ]

    @staticmethod
    def _parents(node):
        while node.parent is not None:
            node = node.parent
            yield node
