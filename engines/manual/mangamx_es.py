try:
    from .base import FuenteBaseSource, _Node, _TreeParser
except ImportError:
    pass

class FuenteBaseSource:
    pass


class MangamxSource(FuenteBaseSource):
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
        data = [
                {
                                "type": "checkbox",
                                "id": "pref_adult",
                                "name": "Show Adult Content",
                                "default": false
                }
]
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
                                                                "name": "Estado",
                                                                "value": "false"
                                                },
                                                {
                                                                "name": "En desarrollo",
                                                                "value": "1"
                                                },
                                                {
                                                                "name": "Completo",
                                                                "value": "0"
                                                },
                                                {
                                                                "name": "Todo",
                                                                "value": "false"
                                                },
                                                {
                                                                "name": "Mangas",
                                                                "value": "0"
                                                },
                                                {
                                                                "name": "Manhwas",
                                                                "value": "1"
                                                },
                                                {
                                                                "name": "One Shot",
                                                                "value": "2"
                                                },
                                                {
                                                                "name": "Manhuas",
                                                                "value": "3"
                                                },
                                                {
                                                                "name": "Novelas",
                                                                "value": "4"
                                                },
                                                {
                                                                "name": "Todos",
                                                                "value": "false"
                                                },
                                                {
                                                                "name": "Comedia",
                                                                "value": "1"
                                                },
                                                {
                                                                "name": "Drama",
                                                                "value": "2"
                                                },
                                                {
                                                                "name": "Acción",
                                                                "value": "3"
                                                },
                                                {
                                                                "name": "Escolar",
                                                                "value": "4"
                                                },
                                                {
                                                                "name": "Romance",
                                                                "value": "5"
                                                },
                                                {
                                                                "name": "Ecchi",
                                                                "value": "6"
                                                },
                                                {
                                                                "name": "Aventura",
                                                                "value": "7"
                                                },
                                                {
                                                                "name": "Shōnen",
                                                                "value": "8"
                                                },
                                                {
                                                                "name": "Shōjo",
                                                                "value": "9"
                                                },
                                                {
                                                                "name": "Deportes",
                                                                "value": "10"
                                                },
                                                {
                                                                "name": "Psicológico",
                                                                "value": "11"
                                                },
                                                {
                                                                "name": "Fantasía",
                                                                "value": "12"
                                                },
                                                {
                                                                "name": "Mecha",
                                                                "value": "13"
                                                },
                                                {
                                                                "name": "Gore",
                                                                "value": "14"
                                                },
                                                {
                                                                "name": "Yaoi",
                                                                "value": "15"
                                                },
                                                {
                                                                "name": "Yuri",
                                                                "value": "16"
                                                },
                                                {
                                                                "name": "Misterio",
                                                                "value": "17"
                                                },
                                                {
                                                                "name": "Sobrenatural",
                                                                "value": "18"
                                                },
                                                {
                                                                "name": "Seinen",
                                                                "value": "19"
                                                },
                                                {
                                                                "name": "Ficción",
                                                                "value": "20"
                                                },
                                                {
                                                                "name": "Harem",
                                                                "value": "21"
                                                },
                                                {
                                                                "name": "Webtoon",
                                                                "value": "25"
                                                },
                                                {
                                                                "name": "Histórico",
                                                                "value": "27"
                                                },
                                                {
                                                                "name": "Músical",
                                                                "value": "30"
                                                },
                                                {
                                                                "name": "Ciencia ficción",
                                                                "value": "31"
                                                },
                                                {
                                                                "name": "Shōjo-ai",
                                                                "value": "32"
                                                },
                                                {
                                                                "name": "Josei",
                                                                "value": "33"
                                                },
                                                {
                                                                "name": "Magia",
                                                                "value": "34"
                                                },
                                                {
                                                                "name": "Artes Marciales",
                                                                "value": "35"
                                                },
                                                {
                                                                "name": "Horror",
                                                                "value": "36"
                                                },
                                                {
                                                                "name": "Demonios",
                                                                "value": "37"
                                                },
                                                {
                                                                "name": "Supervivencia",
                                                                "value": "38"
                                                },
                                                {
                                                                "name": "Recuentos de la vida",
                                                                "value": "39"
                                                },
                                                {
                                                                "name": "Shōnen ai",
                                                                "value": "40"
                                                },
                                                {
                                                                "name": "Militar",
                                                                "value": "41"
                                                },
                                                {
                                                                "name": "Eroge",
                                                                "value": "42"
                                                },
                                                {
                                                                "name": "Isekai",
                                                                "value": "43"
                                                },
                                                {
                                                                "name": "Mostrar todo",
                                                                "value": "false"
                                                },
                                                {
                                                                "name": "Mostrar solo +18",
                                                                "value": "1"
                                                },
                                                {
                                                                "name": "No mostrar +18",
                                                                "value": "0"
                                                }
                                ],
                                "default": "false"
                }
]
        return [SourceFilter(**item) for item in data]

    name = 'mangamx_es'
    display_name = 'MangaOni'
    base_url = 'https://manga-oni.com'
    language = 'es'
    requests_per_minute = 60


SOURCE = MangamxSource
