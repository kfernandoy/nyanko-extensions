"""Implementación común de sitios UzayManga/SvelteKit para Nyanko Source v4."""

from urllib.parse import urljoin

try:
    from .madara_details import MadaraDetailsSource, SourceChapter, SourcePage, SourceSeries
except ImportError:
    pass


class _SvelteData:
    def __init__(self, values: list) -> None:
        self.values = values

    def object(self, index: int) -> dict | None:
        value = self._get(index)
        return value if isinstance(value, dict) else None

    def array(self, index: int) -> list | None:
        value = self._get(index)
        return value if isinstance(value, list) else None

    def string(self, index: int) -> str | None:
        value = self._get(index)
        return str(value) if isinstance(value, (str, int, float)) else None

    def integer(self, index: int) -> int | None:
        value = self._get(index)
        return value if isinstance(value, int) else None

    def resolve_object(self, node: dict, key: str) -> dict | None:
        return self.object(node[key]) if isinstance(node.get(key), int) else None

    def resolve_array(self, node: dict, key: str) -> list | None:
        return self.array(node[key]) if isinstance(node.get(key), int) else None

    def resolve_string(self, node: dict, key: str) -> str | None:
        return self.string(node[key]) if isinstance(node.get(key), int) else None

    def resolve_integer(self, node: dict, key: str) -> int | None:
        return self.integer(node[key]) if isinstance(node.get(key), int) else None

    def _get(self, index: int):
        return self.values[index] if 0 <= index < len(self.values) else None


class UzayMangaSource(MadaraDetailsSource):
    cdn_url = ""
    requests_per_minute = 180

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        return (await self._catalog({"page": "1", "search": query.strip()}))[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"}:
            return []
        return await self._catalog(
            {"sort": "popular" if kind == "popular" else "new", "page": str(page)}
        )

    async def _catalog(self, params: dict[str, str]) -> list[SourceSeries]:
        params["x-sveltekit-invalidated"] = "001"
        response = await self._request(
            "GET",
            f"{self.base_url}/manga/__data.json",
            params=params,
        )
        response.raise_for_status()
        data = self._data(response.json())
        if data is None:
            return []
        root = data.object(0)
        series = data.resolve_array(root, "series") if root else None
        result: list[SourceSeries] = []
        for reference in series or []:
            manga = data.object(reference) if isinstance(reference, int) else None
            if manga is None:
                continue
            title = data.resolve_string(manga, "name")
            slug = data.resolve_string(manga, "slug")
            image_path = data.resolve_string(manga, "image") or ""
            if title and slug:
                result.append(
                    SourceSeries(
                        source_id=f"{self.base_url}/manga/{slug}",
                        title=title,
                        source_name=self.name,
                        cover_url=self._image(image_path),
                    )
                )
        return result

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request(
            "GET",
            f"{series_id.rstrip('/')}/__data.json",
            params={"x-sveltekit-invalidated": "001"},
        )
        response.raise_for_status()
        data = self._data(response.json())
        root = data.object(0) if data else None
        manga = data.resolve_object(root, "series") if data and root else None
        if not data or not manga:
            return series if isinstance(series, SourceSeries) else SourceSeries(series_id, series_id, self.name)
        categories = data.resolve_array(manga, "categories") or []
        genres: list[str] = []
        for reference in categories:
            category = data.object(reference) if isinstance(reference, int) else None
            name = data.resolve_string(category, "name") if category else None
            if name:
                genres.append(name)
        status = {1: "ongoing", 2: "completed", 3: "hiatus"}.get(
            data.resolve_integer(manga, "status")
        )
        title = data.resolve_string(manga, "name")
        return SourceSeries(
            source_id=series_id,
            title=title or (series.title if isinstance(series, SourceSeries) else series_id),
            source_name=self.name,
            cover_url=self._image(data.resolve_string(manga, "image") or ""),
            description=data.resolve_string(manga, "description"),
            status=status,
            content_tags=tuple(dict.fromkeys(genres)),
            metadata=series.metadata if isinstance(series, SourceSeries) else {},
            web_url=series_id,
        )

    def _image(self, path: str) -> str | None:
        if not path:
            return None
        if path.startswith("http"):
            return path
        return urljoin((self.cdn_url or self.base_url).rstrip("/") + "/", path.lstrip("/"))

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        response = await self._request(
            "GET",
            f"{series_id.rstrip('/')}/__data.json",
            params={"x-sveltekit-invalidated": "001"},
        )
        response.raise_for_status()
        data = self._data(response.json())
        if data is None:
            return []
        root = data.object(0)
        manga = data.resolve_object(root, "series") if root else None
        slug = data.resolve_string(manga, "slug") if manga else None
        chapters = data.resolve_array(manga, "SeriesEpisode") if manga else None
        if not slug:
            return []
        result: list[SourceChapter] = []
        for reference in chapters or []:
            item = data.object(reference) if isinstance(reference, int) else None
            if item is None:
                continue
            chapter_slug = data.resolve_string(item, "slug")
            order = data.resolve_string(item, "order")
            name = data.resolve_string(item, "name")
            if not chapter_slug:
                continue
            clean_order = order.removesuffix(".0") if order else ""
            title = f"Bölüm {clean_order}" if clean_order else "Bölüm"
            if name and name != order:
                title += f" - {name}"
            try:
                number = float(clean_order) if clean_order else None
            except ValueError:
                number = None
            result.append(
                SourceChapter(
                    source_id=f"{self.base_url}/manga/{slug}/{chapter_slug}",
                    title=title,
                    series_id=series_id,
                    source_name=self.name,
                    number=number,
                )
            )
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        response = await self._request(
            "GET",
            f"{chapter_id.rstrip('/')}/__data.json",
            params={"x-sveltekit-invalidated": "001"},
        )
        response.raise_for_status()
        data = self._data(response.json())
        if data is None:
            return []
        root = data.object(0)
        episode = data.resolve_object(root, "episode") if root else None
        images = data.resolve_array(episode, "images") if episode else None
        base = (self.cdn_url or self.base_url).rstrip("/") + "/"
        urls = [
            urljoin(base, path)
            for reference in images or []
            if isinstance(reference, int)
            and (path := data.string(reference))
        ]
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
    def _data(payload: dict) -> _SvelteData | None:
        nodes = payload.get("nodes") or []
        node = next(
            (
                item
                for item in reversed(nodes)
                if isinstance(item, dict)
                and item.get("type") == "data"
                and isinstance(item.get("data"), list)
            ),
            None,
        )
        return _SvelteData(node["data"]) if node else None
