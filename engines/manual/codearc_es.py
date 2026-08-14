try:
    from .base import FuenteBaseSource, _Node, _TreeParser
except ImportError:
    pass

class FuenteBaseSource:
    pass


class CodearcSource(FuenteBaseSource):
    search_paths: tuple[str, ...] = ("search", "")
    popular_paths: tuple[str, ...] = ("series", "manga", "comics", "popular", "")
    latest_paths: tuple[str, ...] = ("latest", "updates", "series", "manga", "")

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        for path in self.search_paths:
            for key in ("q", "query", "s", "keyword"):
                try:
                    response = await self._request(
                        "GET",
                        urljoin(f"{self.base_url}/", path),
                        params={key: query.strip(), "page": "1"},
                    )
                    if getattr(response, "status_code", 200) >= 400:
                        continue
                    values = self._adaptive_series(response)
                    if values:
                        return values[:limit]
                except Exception:
                    continue
        return []

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"}:
            return []
        paths = self.popular_paths if kind == "popular" else self.latest_paths
        for path in paths:
            try:
                response = await self._request(
                    "GET",
                    urljoin(f"{self.base_url}/", path),
                    params={"page": str(page)},
                )
                if getattr(response, "status_code", 200) >= 400:
                    continue
                values = self._adaptive_series(response)
                if values:
                    return values
            except Exception:
                continue
        return []

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        result: list[SourceChapter] = []
        for anchor in root.descendants("a"):
            href = anchor.attrs.get("href", "")
            title = anchor.text().strip() or anchor.attrs.get("title", "").strip()
            marker = f"{href} {title}".lower()
            if not href or not any(value in marker for value in ("chapter", "chap", "capitulo", "capítulo", "episode", "bolum", "read/")):
                continue
            found = re.search(r"\d+(?:\.\d+)?", title)
            result.append(
                SourceChapter(
                    source_id=urljoin(str(response.url), href),
                    title=title or "Capítulo",
                    series_id=series_id,
                    source_name=self.name,
                    number=float(found.group()) if found else None,
                )
            )
        if not result:
            try:
                payload = response.json()
            except (ValueError, AttributeError):
                payload = None
            for item in self._walk_dicts(payload):
                title = str(item.get("title") or item.get("name") or "")
                item_id = item.get("url") or item.get("slug") or item.get("id")
                if not title or item_id is None or "chap" not in json.dumps(item).lower():
                    continue
                found = re.search(r"\d+(?:\.\d+)?", title)
                result.append(
                    SourceChapter(
                        source_id=urljoin(str(response.url), str(item_id)),
                        title=title,
                        series_id=series_id,
                        source_name=self.name,
                        number=float(found.group()) if found else None,
                    )
                )
        return list({item.source_id: item for item in result}.values())

    def _adaptive_series(self, response) -> list[SourceSeries]:
        root = _parse_html(response.text)
        result: list[SourceSeries] = []
        seen: set[str] = set()
        for anchor in root.descendants("a"):
            href = anchor.attrs.get("href", "")
            title = anchor.attrs.get("title", "").strip() or anchor.text().strip()
            parent = anchor.parent
            marker = ""
            while parent is not None:
                marker += f" {parent.attrs.get('id', '')} {parent.attrs.get('class', '')}"
                parent = parent.parent
            if not href or not title or not any(value in marker.lower() for value in ("manga", "comic", "series", "novel", "item", "book")):
                continue
            source_id = urljoin(str(response.url), href)
            if source_id not in seen:
                seen.add(source_id)
                image = _first(anchor, lambda node: node.tag == "img")
                if image is None and anchor.parent is not None:
                    image = _first(anchor.parent, lambda node: node.tag == "img")
                result.append(
                    SourceSeries(
                        source_id=source_id,
                        title=title,
                        source_name=self.name,
                        cover_url=(
                            _image_url(image, str(response.url)) if image else None
                        ),
                        web_url=source_id,
                    )
                )
        if result:
            return result
        try:
            payload = response.json()
        except (ValueError, AttributeError):
            return []
        for item in self._walk_dicts(payload):
            title = item.get("title") or item.get("name")
            item_id = item.get("url") or item.get("href") or item.get("slug") or item.get("id")
            if title and item_id is not None:
                source_id = urljoin(str(response.url), str(item_id))
                if source_id not in seen:
                    seen.add(source_id)
                    cover = (
                        item.get("cover_url")
                        or item.get("cover")
                        or item.get("thumbnail")
                        or item.get("image")
                    )
                    result.append(
                        SourceSeries(
                            source_id=source_id,
                            title=str(title),
                            source_name=self.name,
                            cover_url=(
                                urljoin(str(response.url), cover)
                                if isinstance(cover, str)
                                else None
                            ),
                            web_url=source_id,
                        )
                    )
        return result

    @staticmethod
    def _walk_dicts(value):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from GenericSource._walk_dicts(child)
        elif isinstance(value, list):
            for child in value:
                yield from GenericSource._walk_dicts(child)

class GeneratedGenericSource(GenericSource):

    def get_preferences(self) -> list[SourcePreference]:
        # Autogenerated via heuristic port
        data = []
        return [SourcePreference(**item) for item in data]

    def get_filters(self) -> list[SourceFilter]:
        # Autogenerated via heuristic port
        data = [
                {
                                "type": "select",
                                "id": "generic_filter",
                                "name": "Filtro",
                                "options": [
                                                {
                                                                "name": "Doujinshi",
                                                                "value": "1"
                                                },
                                                {
                                                                "name": "Manga",
                                                                "value": "2"
                                                },
                                                {
                                                                "name": "Manhwa",
                                                                "value": "3"
                                                },
                                                {
                                                                "name": "Manhua",
                                                                "value": "4"
                                                },
                                                {
                                                                "name": "Comic",
                                                                "value": "5"
                                                },
                                                {
                                                                "name": "Recopilación",
                                                                "value": "6"
                                                },
                                                {
                                                                "name": "Artista CG",
                                                                "value": "7"
                                                },
                                                {
                                                                "name": "Serie y One-shot",
                                                                "value": "both"
                                                },
                                                {
                                                                "name": "Solo Serie",
                                                                "value": "serie"
                                                },
                                                {
                                                                "name": "Solo One-shot",
                                                                "value": "oneshot"
                                                },
                                                {
                                                                "name": "Mas recientes",
                                                                "value": "latest"
                                                },
                                                {
                                                                "name": "Mas popular del dia",
                                                                "value": "popular_day"
                                                },
                                                {
                                                                "name": "Mas popular de la semana",
                                                                "value": "popular_week"
                                                },
                                                {
                                                                "name": "Mas popular del mes",
                                                                "value": "popular_month"
                                                },
                                                {
                                                                "name": "Mas popular desde siempre",
                                                                "value": "popular_all"
                                                }
                                ],
                                "default": "1"
                }
]
        return [SourceFilter(**item) for item in data]

    name = 'codearc_es'
    display_name = 'Code Arc Mangas'
    base_url = 'https://mangas.codearctraducciones.com'
    language = 'es'
    requests_per_minute = 60


class CodeArcSource(GeneratedGenericSource):
    async def get_filters(self) -> list[SourceFilter]:
        response = await self._request("GET", f"{self.base_url}/list")
        response.raise_for_status()
        root = _parse_html(response.text)
        genres = {}
        for anchor in root.descendants("a"):
            values = parse_qs(urlparse(anchor.attrs.get("href", "")).query).get("generos")
            if values and values[0]:
                genres[values[0]] = anchor.text().strip() or values[0]
        return [
            SourceFilter("content_type", "Tipo de contenido", "select", [
                ("", "Todo tipo de contenido"), ("1", "Doujinshi"), ("2", "Manga"),
                ("3", "Manhwa"), ("4", "Manhua"), ("5", "Comic"),
                ("6", "Recopilación"), ("7", "Artista CG"),
            ], ""),
            SourceFilter("format", "Formato", "select", [
                ("both", "Serie y One-shot"), ("serie", "Solo Serie"), ("oneshot", "Solo One-shot"),
            ], "both"),
            SourceFilter("sort", "Ordenar por", "select", [
                ("latest", "Más recientes"), ("popular_day", "Popular del día"),
                ("popular_week", "Popular de la semana"), ("popular_month", "Popular del mes"),
                ("popular_all", "Popular desde siempre"),
            ], "latest"),
            SourceFilter("genres", "Géneros", "multi_select", sorted(genres.items(), key=lambda item: item[1]), []),
        ]

    @staticmethod
    def _classes(node, *names):
        values = node.attrs.get("class", "").split()
        return all(name in values for name in names)

    @staticmethod
    def _has_next(root) -> bool:
        return any(
            node.attrs.get("aria-label") == "Pagina siguiente" and "disabled" not in node.attrs
            for node in root.descendants() if node.tag in {"a", "button"}
        )

    def _html_listing(self, response, latest: bool) -> dict:
        root = _parse_html(response.text)
        required = ("group", "overflow-hidden") if latest else ("group", "relative", "min-w-0")
        items = []
        for anchor in root.descendants("a"):
            if not self._classes(anchor, *required) or not anchor.attrs.get("href"):
                continue
            title_node = _first(anchor, lambda node: self._classes(node, "line-clamp-2") if latest else self._classes(node, "truncate", "text-base"))
            title = title_node.text().strip() if title_node else anchor.attrs.get("aria-label", "").strip()
            if not title:
                continue
            image = _first(anchor, lambda node: node.tag == "img")
            source_id = urljoin(str(response.url), anchor.attrs["href"])
            items.append(SourceSeries(
                source_id=source_id, title=title, source_name=self.name,
                cover_url=_image_url(image, str(response.url)) if image else None,
                web_url=source_id,
            ))
        return {"items": items, "has_more": bool(items) and self._has_next(root)}

    async def browse(self, kind: str, page: int = 1):
        if kind == "popular":
            response = await self._request("GET", f"{self.base_url}/ranking", params={"mode": "popular", "page": str(page)})
            latest = False
        elif kind == "latest":
            response = await self._request("GET", f"{self.base_url}/list", params={"page": str(page)})
            latest = True
        else:
            return {"items": [], "has_more": False}
        response.raise_for_status()
        result = self._html_listing(response, latest)
        if kind == "popular" and page >= 5:
            result["has_more"] = False
        return result

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        filters = filters or {}
        genre_values = filters.get("genres", [])
        genres = ",".join(genre_values) if isinstance(genre_values, list) else str(genre_values).strip()
        custom = any(filters.get(key, default) != default for key, default in {
            "content_type": "", "format": "both", "sort": "latest"
        }.items()) or bool(genres)
        if query.strip() and not custom:
            response = await self._request(
                "GET", f"{self.base_url}/api/mangas/search",
                params={"q": query.strip(), "limit": "50"},
            )
            response.raise_for_status()
            payload = response.json() if hasattr(response, "json") else json.loads(response.text)
            items = []
            for value in payload.get("items", []):
                slug = str(value.get("slug", "")).lstrip("/")
                source_id = f"{self.base_url}/{slug}"
                cover = value.get("portada")
                items.append(SourceSeries(
                    source_id=source_id, title=str(value.get("titulo", "")), source_name=self.name,
                    cover_url=urljoin(f"{self.base_url}/", cover) if cover else None, web_url=source_id,
                ))
            return {"items": items, "has_more": False}
        params = {"page": str(page)}
        if query.strip(): params["q"] = query.strip()
        if filters.get("content_type"): params["tipo"] = str(filters["content_type"])
        if filters.get("format", "both") != "both": params["formato"] = str(filters["format"])
        if filters.get("sort", "latest") != "latest": params["sort"] = str(filters["sort"])
        if genres: params["generos"] = genres
        response = await self._request("GET", f"{self.base_url}/list", params=params)
        response.raise_for_status()
        return self._html_listing(response, True)

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        response = await self._request("GET", series_id)
        response.raise_for_status()
        root = _parse_html(response.text)
        anchors = [node for node in root.descendants("a") if "/reader/" in node.attrs.get("href", "") and "/cascade" in node.attrs.get("href", "")]
        if not anchors:
            return []
        result = []
        for anchor in anchors:
            source_id = urljoin(str(response.url), anchor.attrs["href"])
            number = re.search(r"/reader/[^/]+/(\d+(?:\.\d+)?)/", source_id)
            heading = _first(anchor, lambda node: node.tag == "h3")
            value = float(number.group(1)) if number else 1.0
            result.append(SourceChapter(
                source_id=source_id, title=heading.text().strip() if heading else f"Chapter {value:g}",
                series_id=series_id, source_name=self.name, number=value, language=self.language,
            ))
        return list({chapter.source_id: chapter for chapter in result}.values())

    @classmethod
    def _reader_data(cls, text: str) -> dict:
        decoder = json.JSONDecoder()
        for index, char in enumerate(text):
            if char not in "[{": continue
            try: value, _ = decoder.raw_decode(text[index:])
            except json.JSONDecodeError: continue
            for item in cls._walk_dicts(value):
                if {"initialPages", "totalPages", "pagesFetchUrl"} <= item.keys():
                    return item
        return {}

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        response = await self._request("GET", chapter_id, headers={"RSC": "1"})
        response.raise_for_status()
        data = self._reader_data(response.text)
        pages = list(data.get("initialPages", []))
        total = int(data.get("totalPages", len(pages)))
        fetch_url = urljoin(self.base_url, str(data.get("pagesFetchUrl", "")))
        while fetch_url and len(pages) < total:
            next_response = await self._request("GET", fetch_url, params={"offset": str(len(pages))})
            next_response.raise_for_status()
            payload = next_response.json() if hasattr(next_response, "json") else json.loads(next_response.text)
            new_pages = payload.get("items", [])
            if not new_pages: break
            pages.extend(new_pages)
        urls = [str(value.get("imagen_url", "")) for value in pages if value.get("imagen_url")]
        return [SourcePage(
            source_id=url, chapter_id=chapter_id, index=index,
            filename=url.rsplit("/", 1)[-1].split("?", 1)[0] or f"{index}.jpg", source_name=self.name,
        ) for index, url in enumerate(urls)]


SOURCE = CodearcSource
