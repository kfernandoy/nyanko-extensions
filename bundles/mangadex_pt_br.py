from __future__ import annotations

"""Adaptador de MangaDex para Nyanko Source API v3."""

from typing import Any

from nyanko_api.sources.contract import (
    SOURCE_API_VERSION,
    SourceCapabilities,
    SourceChapter,
    SourceFetcher,
    SourcePage,
    SourcePageContent,
    SourceSeries,
)
from nyanko_api.sources.errors import SourceNotFoundError

API_URL = "https://api.mangadex.org"
CONTENT_RATINGS = ("safe", "suggestive")


class MangaDexSource:
    name = "mangadex"
    display_name = "MangaDex"
    language = "en"
    languages = ("en",)
    api_version = SOURCE_API_VERSION
    content_warning = "safe"

    def __init__(self, fetcher: SourceFetcher | None = None) -> None:
        self.fetcher = fetcher
        self.capabilities = SourceCapabilities(
            search=True,
            browse=True,
            headers={
                "User-Agent": "Nyanko/0.2.4",
                "Referer": "https://mangadex.org/",
                "Origin": "https://mangadex.org",
            },
            requests_per_minute=60,
            content_warning=self.content_warning,
        )

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        params = self._manga_params(limit=min(limit, 100))
        params.append(("title", query.strip()))
        payload = await self._get_json("/manga", params)
        return [self._series(item) for item in payload.get("data", [])]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind == "popular":
            params = self._manga_params(limit=20, offset=(page - 1) * 20)
            params.append(("order[followedCount]", "desc"))
            payload = await self._get_json("/manga", params)
            return [self._series(item) for item in payload.get("data", [])]
        if kind == "latest":
            params: list[tuple[str, str | int]] = [
                ("limit", 100),
                ("offset", (page - 1) * 100),
                ("includes[]", "manga"),
                ("order[publishAt]", "desc"),
                ("includeEmptyPages", 0),
            ]
            params.extend(("translatedLanguage[]", lang) for lang in self.languages)
            chapters = await self._get_json("/chapter", params)
            ids = list(
                dict.fromkeys(
                    relation["id"]
                    for chapter in chapters.get("data", [])
                    for relation in chapter.get("relationships", [])
                    if relation.get("type") == "manga"
                )
            )[:20]
            return await self._series_by_ids(ids)
        return []

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        payload = await self._get_json(
            f"/manga/{series_id}",
            [
                ("includes[]", "cover_art"),
                ("includes[]", "author"),
                ("includes[]", "artist"),
            ],
        )
        item = payload.get("data")
        if not isinstance(item, dict):
            raise SourceNotFoundError(f"MangaDex no devolvió la serie: {series_id}")
        return self._series(item)

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        common: list[tuple[str, str | int]] = [
            ("limit", 500),
            ("includes[]", "scanlation_group"),
            ("order[volume]", "desc"),
            ("order[chapter]", "desc"),
        ]
        common.extend(("translatedLanguage[]", lang) for lang in self.languages)
        readable = await self._get_json(f"/manga/{series_id}/feed", [*common, ("includeEmptyPages", 0)])
        empty = await self._get_json(f"/manga/{series_id}/feed", [*common, ("includeEmptyPages", 1)])
        items = list({item["id"]: item for item in [*readable.get("data", []), *empty.get("data", [])]}.values())
        return [self._chapter(item, series_id) for item in items]

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        source_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        chapter_id, _, state = source_id.partition("|")
        if state == "empty":
            return []
        at_home = await self._get_json(f"/at-home/server/{chapter_id}")
        return [
            SourcePage(f"{chapter_id}|{index}", source_id, index + 1, filename, self.name)
            for index, filename in enumerate(at_home.get("chapter", {}).get("data", []))
        ]

    async def page_bytes(self, page: SourcePage | str) -> SourcePageContent:
        source_id = page.source_id if isinstance(page, SourcePage) else page
        chapter_id, separator, raw_index = source_id.rpartition("|")
        if not separator or not raw_index.isdigit():
            raise SourceNotFoundError(f"Página MangaDex inválida: {source_id}")
        at_home = await self._get_json(f"/at-home/server/{chapter_id}")
        chapter = at_home.get("chapter", {})
        filenames = chapter.get("data", [])
        index = int(raw_index)
        if index >= len(filenames):
            raise SourceNotFoundError(f"Página MangaDex fuera de rango: {source_id}")
        response = await self._request("GET", f"{at_home['baseUrl']}/data/{chapter['hash']}/{filenames[index]}")
        response.raise_for_status()
        return SourcePageContent(
            media_type=response.headers.get("Content-Type", "image/jpeg"),
            chunks=iter([response.content]),
        )

    async def _series_by_ids(self, ids: list[str]) -> list[SourceSeries]:
        if not ids:
            return []
        params = self._manga_params(limit=len(ids))
        params.extend(("ids[]", manga_id) for manga_id in ids)
        payload = await self._get_json("/manga", params)
        by_id = {item["id"]: self._series(item) for item in payload.get("data", [])}
        return [by_id[manga_id] for manga_id in ids if manga_id in by_id]

    def _manga_params(self, *, limit: int, offset: int = 0) -> list[tuple[str, str | int]]:
        params: list[tuple[str, str | int]] = [("limit", limit), ("offset", offset), ("includes[]", "cover_art")]
        params.extend(("availableTranslatedLanguage[]", lang) for lang in self.languages)
        params.extend(("contentRating[]", rating) for rating in CONTENT_RATINGS)
        return params

    def _series(self, item: dict[str, Any]) -> SourceSeries:
        manga_id = item["id"]
        attributes = item.get("attributes", {})
        filename = next(
            (
                relation.get("attributes", {}).get("fileName")
                for relation in item.get("relationships", [])
                if relation.get("type") == "cover_art"
            ),
            None,
        )
        return SourceSeries(
            source_id=manga_id,
            title=self._title(attributes),
            source_name=self.name,
            cover_url=(
                f"https://uploads.mangadex.org/covers/{manga_id}/{filename}.256.jpg"
                if filename
                else None
            ),
            description=self._localized(attributes.get("description")) or None,
            author=self._relationship_names(item, "author") or None,
            artist=self._relationship_names(item, "artist") or None,
            status=self._status(attributes.get("status")),
            content_tags=self._tags(attributes),
            metadata=self._metadata(attributes),
            web_url=f"https://mangadex.org/title/{manga_id}",
        )

    def _chapter(self, item: dict[str, Any], series_id: str) -> SourceChapter:
        attributes = item.get("attributes", {})
        number_text = attributes.get("chapter")
        try:
            number = float(number_text) if number_text not in (None, "") else None
        except ValueError:
            number = None
        pages = int(attributes.get("pages") or 0)
        label = f"Capítulo {number_text}" if number_text else "Oneshot"
        if attributes.get("title"):
            label += f" · {attributes['title']}"
        if pages == 0:
            label += " · sin páginas"
        groups = [
            relation.get("attributes", {}).get("name", "")
            for relation in item.get("relationships", [])
            if relation.get("type") == "scanlation_group"
        ]
        return SourceChapter(
            source_id=f"{item['id']}|empty" if pages == 0 else item["id"],
            title=label,
            series_id=series_id,
            source_name=self.name,
            number=number,
            scanlator=" & ".join(filter(None, groups)),
            language=attributes.get("translatedLanguage", ""),
            uploaded_at=attributes.get("publishAt"),
        )

    def _title(self, attributes: dict[str, Any]) -> str:
        titles = attributes.get("title", {})
        alternatives = attributes.get("altTitles", [])
        for language in (*self.languages, "en"):
            if titles.get(language):
                return titles[language]
            for alternative in alternatives:
                if alternative.get(language):
                    return alternative[language]
        return next(iter(titles.values()), "Sin título")

    def _localized(self, values: Any) -> str:
        if not isinstance(values, dict):
            return ""
        for language in (*self.languages, "en"):
            value = values.get(language)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return next(
            (value.strip() for value in values.values() if isinstance(value, str) and value.strip()),
            "",
        )

    @staticmethod
    def _relationship_names(item: dict[str, Any], kind: str) -> str:
        names = dict.fromkeys(
            str(relation.get("attributes", {}).get("name") or "").strip()
            for relation in item.get("relationships", [])
            if relation.get("type") == kind
        )
        return ", ".join(name for name in names if name)

    def _tags(self, attributes: dict[str, Any]) -> tuple[str, ...]:
        names = dict.fromkeys(
            self._localized(tag.get("attributes", {}).get("name"))
            for tag in attributes.get("tags", [])
            if isinstance(tag, dict)
        )
        return tuple(name for name in names if name)

    @staticmethod
    def _status(value: Any) -> str | None:
        status = str(value or "").strip().lower()
        return status if status in {"ongoing", "completed", "hiatus", "cancelled"} else None

    def _metadata(self, attributes: dict[str, Any]) -> dict[str, str]:
        metadata: dict[str, str] = {}
        fields = (
            ("Año", attributes.get("year")),
            ("Idioma original", attributes.get("originalLanguage")),
            ("Demografía", attributes.get("publicationDemographic")),
            ("Clasificación", attributes.get("contentRating")),
            ("Último volumen", attributes.get("lastVolume")),
            ("Último capítulo", attributes.get("lastChapter")),
        )
        for label, value in fields:
            if value not in (None, ""):
                metadata[label] = str(value)

        title = self._title(attributes)
        alternatives = dict.fromkeys(
            self._localized(alternative)
            for alternative in attributes.get("altTitles", [])
            if isinstance(alternative, dict)
        )
        other_titles = [
            alternative for alternative in alternatives if alternative and alternative != title
        ][:3]
        if other_titles:
            metadata["Títulos alternativos"] = ", ".join(other_titles)
        return metadata

    async def _get_json(self, path: str, params: list[tuple[str, str | int]] | None = None) -> dict[str, Any]:
        response = await self._request("GET", f"{API_URL}{path}", params=params)
        response.raise_for_status()
        return response.json()

    async def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        if self.fetcher is None:
            raise SourceNotFoundError("MangaDex no tiene fetcher inyectado")
        return await self.fetcher.request(method, url, **kwargs)

class GeneratedMangaDexSource(MangaDexSource):
    name = 'mangadex_pt_br'
    display_name = 'MangaDex (pt-BR)'
    language = 'pt-BR'
    languages = ('pt-br',)

SOURCE = GeneratedMangaDexSource

"""Puente de contrato para adaptadores que conservan metodos v3."""

import inspect
from collections.abc import Mapping
from typing import Any

from nyanko_api.sources.contract import Paginated, SourceFilter, SourcePreference

_PAGE_SIZE = 20


def _parameters(method: Any) -> Mapping[str, Any]:
    return inspect.signature(method).parameters


def _arguments(method: Any, page: int, filters: Mapping[str, Any] | None) -> dict[str, Any]:
    parameters = _parameters(method)
    arguments: dict[str, Any] = {}
    if "page" in parameters:
        arguments["page"] = page
    if "filters" in parameters:
        arguments["filters"] = filters
    if "limit" in parameters:
        # Un metodo v3 sin `page` solo se controla por `limit`: se pide el
        # acumulado hasta la pagina solicitada y luego se recorta el tramo. El
        # elemento extra es el sondeo que distingue "no hay mas" de "justo cabia".
        arguments["limit"] = _PAGE_SIZE if "page" in parameters else page * _PAGE_SIZE + 1
    return arguments


def _unwrap(value: Any) -> tuple[list[Any], bool | None]:
    """Normaliza un retorno v3 a ``(items, has_more)``; ``None`` si no lo declara."""
    if isinstance(value, Paginated):
        return list(value.items), value.has_more
    if isinstance(value, dict):
        declared = value.get("has_more", value.get("has_next_page"))
        items = value.get("items", value.get("results", []))
        return list(items or []), None if declared is None else bool(declared)
    return list(value or []), None


def _paginated(value: Any, has_more: bool) -> Paginated:
    items, declared = _unwrap(value)
    if declared is not None:
        has_more = declared
    return Paginated(items=items, has_more=has_more and bool(items))


def _window(value: Any, page: int) -> Paginated:
    """Pagina en el cliente un metodo v3 que devuelve el acumulado de una vez."""
    items, declared = _unwrap(value)
    start = (page - 1) * _PAGE_SIZE
    window = items[start : start + _PAGE_SIZE]
    has_more = len(items) > start + _PAGE_SIZE if declared is None else declared
    return Paginated(items=window, has_more=has_more and bool(window))


def _consumes_filters(legacy_source: type) -> bool:
    return any(
        "filters" in _parameters(method)
        for name in ("search", "browse")
        if callable(method := getattr(legacy_source, name, None))
    )


def _options(options: Any) -> list[tuple[str, str]] | None:
    if options is None:
        return None
    return [
        (str(option.get("value", "")), str(option.get("name", "")))
        if isinstance(option, dict)
        else (str(option[0]), str(option[1]))
        for option in options
    ]


def _filters(values: Any) -> list[SourceFilter]:
    return [
        SourceFilter(
            id=value.id,
            name=value.name,
            type="multi_select" if value.type == "group" else value.type,
            options=_options(value.options),
            default=[] if value.type == "group" and not isinstance(value.default, list) else value.default,
        )
        for value in values
    ]


def _preferences(values: Any) -> list[SourcePreference]:
    return [
        SourcePreference(
            id=value.id,
            name=value.name,
            type=value.type,
            options=_options(value.options),
            default=value.default,
        )
        for value in values
    ]


def adapt_source(legacy_source: type) -> type:
    # Un filtro que ningun metodo v3 acepta no se anuncia: la UI mostraria
    # controles que el adaptador descarta en silencio.
    publishes_filters = _consumes_filters(legacy_source)

    class SourceV4(legacy_source):
        async def get_filters(self) -> list[SourceFilter]:
            getter = getattr(super(), "get_filters", None)
            if not getter or not publishes_filters:
                return []
            values = getter()
            if inspect.isawaitable(values):
                values = await values
            return _filters(values)

        def get_preferences(self) -> list[SourcePreference]:
            getter = getattr(super(), "get_preferences", None)
            return _preferences(getter()) if getter else []

        async def search(
            self,
            query: str,
            page: int = 1,
            filters: Mapping[str, Any] | None = None,
        ) -> Paginated:
            method = super().search
            result = await method(query, **_arguments(method, page, filters))
            if "page" in _parameters(method):
                return _paginated(result, True)
            return _window(result, page)

        async def browse(
            self,
            kind: str,
            page: int = 1,
            filters: Mapping[str, Any] | None = None,
        ) -> Paginated:
            method = super().browse
            return _paginated(await method(kind, **_arguments(method, page, filters)), True)

    SourceV4.__name__ = legacy_source.__name__
    SourceV4.__qualname__ = legacy_source.__qualname__
    return SourceV4

SOURCE = adapt_source(SOURCE)
