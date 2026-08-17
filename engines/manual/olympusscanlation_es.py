try:
    from .madara import (
        MadaraSource, _Node, _TreeParser
    )
except ImportError:
    pass

class MadaraSource:
    pass


_OLYMPUS_DIRECTORY = "https://olympus.pages.dev"
_OLYMPUS_CACHE_SECONDS = 60 * 60
_OLYMPUS_PAGE = 20
_OLYMPUS_STATUS = {1: "ongoing", 3: "hiatus", 4: "completed", 5: "cancelled"}


class OlympusscanlationSource(MadaraSource):
    """El slug de cada serie no viaja en la ficha: se aprende del listado."""

    fetch_domain = True

    def __init__(self, fetcher: SourceFetcher | None = None) -> None:
        super().__init__(fetcher)
        self._series_cache: list[dict] = []
        self._series_at = 0.0
        self._slugs: dict[int, str] = {}
        self._domain_checked = False

    @property
    def api_url(self) -> str:
        return self.base_url.replace("https://", "https://panel.")

    def get_preferences(self) -> list[SourcePreference]:
        return [
            SourcePreference(
                "fetchDomain", "Buscar dominio automáticamente", "checkbox", default=True,
            )
        ]

    def get_filters(self) -> list[SourceFilter]:
        return []

    async def browse(self, kind: str, page: int = 1):
        await self._ensure_series()
        if kind == "popular":
            payload = await self._get(f"{self.base_url}/api/rankings", {
                "page": str(page), "period": "total_ranking",
            })
        elif kind == "latest":
            payload = await self._get(f"{self.base_url}/api/new-chapters", {"page": str(page)})
        else:
            return {"items": [], "has_more": False}
        items = [
            self._series(item)
            for item in payload.get("data") or []
            if isinstance(item, dict) and item.get("type") == "comic"
        ]
        return {
            "items": items,
            "has_more": int(payload.get("current_page") or 0) < int(payload.get("last_page") or 0),
        }

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        # No hay endpoint de busqueda: se filtra el listado completo cacheado.
        await self._ensure_series()
        needle = query.strip().casefold()
        matches = [
            item for item in self._series_cache
            if needle in str(item.get("name") or "").casefold()
        ]
        start = (page - 1) * _OLYMPUS_PAGE
        return {
            "items": [self._series(item) for item in matches[start : start + _OLYMPUS_PAGE]],
            "has_more": page * _OLYMPUS_PAGE < len(matches),
        }

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        slug = await self._slug(series_id)
        payload = await self._get(f"{self.base_url}/api/series/{slug}", {"type": "comic"})
        data = payload.get("data") or {}
        return SourceSeries(
            source_id=str(data.get("id") or series_id),
            title=str(data.get("name") or ""),
            source_name=self.name,
            cover_url=data.get("cover") or None,
            description=str(data.get("summary") or "") or None,
            status=_OLYMPUS_STATUS.get(int((data.get("status") or {}).get("id") or 0)),
            content_tags=tuple(
                str(genre.get("name") or "").strip()
                for genre in data.get("genres") or []
                if isinstance(genre, dict)
            ),
            web_url=f"{self.base_url}/series/comic-{slug}",
        )

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        slug = await self._slug(series_id)
        entries: list[dict] = []
        total, page = None, 1
        while True:
            payload = await self._get(f"{self.api_url}/api/series/{slug}/chapters", {
                "page": str(page), "direction": "desc", "type": "comic",
            })
            batch = [item for item in payload.get("data") or [] if isinstance(item, dict)]
            entries.extend(batch)
            total = int((payload.get("meta") or {}).get("total") or 0) if total is None else total
            if not batch or len(entries) >= total:
                break
            page += 1
        return [
            SourceChapter(
                source_id=f"{series_id}/{item.get('id')}",
                title=f"Capitulo {item.get('name')}",
                series_id=series_id,
                source_name=self.name,
                number=self._float(item.get("name")),
                language=self.language,
                uploaded_at=self._date(item.get("published_at")),
            )
            for item in entries
        ]

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else str(chapter)
        series_id, _, identifier = chapter_id.partition("/")
        slug = await self._slug(series_id)
        payload = await self._get(f"{self.base_url}/api/capitulo/comic-{slug}/{identifier}", {})
        urls = [str(value) for value in (payload.get("chapter") or {}).get("pages") or []]
        return [
            SourcePage(
                source_id=value,
                chapter_id=chapter_id,
                index=index,
                filename=urlparse(value).path.rsplit("/", 1)[-1] or f"{index}.jpg",
                source_name=self.name,
            )
            for index, value in enumerate(urls)
        ]

    # -------------------------------------------------------------- internals
    async def _ensure_domain(self) -> None:
        if self._domain_checked or not self.fetch_domain:
            return
        self._domain_checked = True
        try:
            response = await self._request("GET", _OLYMPUS_DIRECTORY)
            response.raise_for_status()
            root = _parse_html(response.text)
            meta = _first(
                root,
                lambda node: node.tag == "meta" and node.attrs.get("property") == "og:url",
            )
            target = meta.attrs.get("content", "") if meta is not None else ""
            if not target:
                return
            resolved = await self._request("GET", target)
            host = urlparse(str(resolved.url) or target).netloc
            if host:
                # El dominio cambia a menudo; la app no persiste, se usa por sesion.
                self.base_url = f"https://{host}"
        except Exception:
            return

    async def _ensure_series(self) -> None:
        import time

        await self._ensure_domain()
        now = time.time()
        if self._series_cache and now - self._series_at < _OLYMPUS_CACHE_SECONDS:
            return
        payload = await self._get(f"{self.base_url}/api/series/list", {})
        comics = [
            item
            for item in payload.get("data") or []
            if isinstance(item, dict) and item.get("type") == "comic"
        ]
        self._series_cache = comics
        self._series_at = now
        self._slugs.update(
            {int(item["id"]): str(item.get("slug") or "") for item in comics if item.get("id") is not None}
        )

    async def _slug(self, series_id: str) -> str:
        await self._ensure_series()
        try:
            key = int(series_id)
        except (TypeError, ValueError):
            return series_id
        slug = self._slugs.get(key)
        if not slug:
            raise SourceNotFoundError(f"{self.display_name}: serie {series_id} sin slug conocido")
        return slug

    async def _get(self, url: str, params: dict) -> dict:
        response = await self._request("GET", url, params=params)
        response.raise_for_status()
        return response.json() or {}

    def _series(self, item: dict) -> SourceSeries:
        identifier = item.get("id")
        if identifier is not None and item.get("slug"):
            self._slugs[int(identifier)] = str(item["slug"])
        return SourceSeries(
            source_id=str(identifier),
            title=str(item.get("name") or ""),
            source_name=self.name,
            cover_url=item.get("cover") or None,
            web_url=f"{self.base_url}/series/comic-{item.get('slug')}",
        )

    @staticmethod
    def _float(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

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




SOURCE = OlympusscanlationSource
