try:
    from .madara import MadaraSource, _Node, _TreeParser
except ImportError:
    pass

class MadaraSource:
    pass


class LeercapituloSource(MadaraSource):
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
                                                                "name": "Acción",
                                                                "value": "accion"
                                                },
                                                {
                                                                "name": "Animación",
                                                                "value": "animacion"
                                                },
                                                {
                                                                "name": "Apocalíptico",
                                                                "value": "apocaliptico"
                                                },
                                                {
                                                                "name": "Artes Marciales",
                                                                "value": "artes-marciales"
                                                },
                                                {
                                                                "name": "Aventura",
                                                                "value": "aventura"
                                                },
                                                {
                                                                "name": "Boys Love",
                                                                "value": "boys-love"
                                                },
                                                {
                                                                "name": "Ciberpunk",
                                                                "value": "ciberpunk"
                                                },
                                                {
                                                                "name": "Ciencia Ficción",
                                                                "value": "ciencia-ficcion"
                                                },
                                                {
                                                                "name": "Comedia",
                                                                "value": "comedia"
                                                },
                                                {
                                                                "name": "Crimen",
                                                                "value": "crimen"
                                                },
                                                {
                                                                "name": "Demonios",
                                                                "value": "demonios"
                                                },
                                                {
                                                                "name": "Deporte",
                                                                "value": "deporte"
                                                },
                                                {
                                                                "name": "Drama",
                                                                "value": "drama"
                                                },
                                                {
                                                                "name": "Ecchi",
                                                                "value": "ecchi"
                                                },
                                                {
                                                                "name": "Extranjero",
                                                                "value": "extranjero"
                                                },
                                                {
                                                                "name": "Familia",
                                                                "value": "familia"
                                                },
                                                {
                                                                "name": "Fantasia",
                                                                "value": "fantasia"
                                                },
                                                {
                                                                "name": "Género Bender",
                                                                "value": "genero-bender"
                                                },
                                                {
                                                                "name": "Girls Love",
                                                                "value": "girls-love"
                                                },
                                                {
                                                                "name": "Gore",
                                                                "value": "gore"
                                                },
                                                {
                                                                "name": "Guerra",
                                                                "value": "guerra"
                                                },
                                                {
                                                                "name": "Harem",
                                                                "value": "harem"
                                                },
                                                {
                                                                "name": "Historia",
                                                                "value": "historia"
                                                },
                                                {
                                                                "name": "Horror",
                                                                "value": "horror"
                                                },
                                                {
                                                                "name": "Magia",
                                                                "value": "magia"
                                                },
                                                {
                                                                "name": "Mecha",
                                                                "value": "mecha"
                                                },
                                                {
                                                                "name": "Militar",
                                                                "value": "militar"
                                                },
                                                {
                                                                "name": "Misterio",
                                                                "value": "misterio"
                                                },
                                                {
                                                                "name": "Musica",
                                                                "value": "musica"
                                                },
                                                {
                                                                "name": "Niños",
                                                                "value": "ninos"
                                                },
                                                {
                                                                "name": "Oeste",
                                                                "value": "oeste"
                                                },
                                                {
                                                                "name": "Parodia",
                                                                "value": "parodia"
                                                },
                                                {
                                                                "name": "Policiaco",
                                                                "value": "policiaco"
                                                },
                                                {
                                                                "name": "Psicológico",
                                                                "value": "psicologico"
                                                },
                                                {
                                                                "name": "Realidad",
                                                                "value": "realidad"
                                                },
                                                {
                                                                "name": "Realidad Virtual",
                                                                "value": "realidad-virtual"
                                                },
                                                {
                                                                "name": "Recuentos de la vida",
                                                                "value": "recuentos-de-la-vida"
                                                },
                                                {
                                                                "name": "Reencarnación",
                                                                "value": "reencarnacion"
                                                },
                                                {
                                                                "name": "Romance",
                                                                "value": "romance"
                                                },
                                                {
                                                                "name": "Samurái",
                                                                "value": "samurai"
                                                },
                                                {
                                                                "name": "Sobrenatural",
                                                                "value": "sobrenatural"
                                                },
                                                {
                                                                "name": "Superpoderes",
                                                                "value": "superpoderes"
                                                },
                                                {
                                                                "name": "Supervivencia",
                                                                "value": "supervivencia"
                                                },
                                                {
                                                                "name": "Telenovela",
                                                                "value": "telenovela"
                                                },
                                                {
                                                                "name": "Thriller",
                                                                "value": "thriller"
                                                },
                                                {
                                                                "name": "Tragedia",
                                                                "value": "tragedia"
                                                },
                                                {
                                                                "name": "Traps",
                                                                "value": "traps"
                                                },
                                                {
                                                                "name": "Vampiros",
                                                                "value": "vampiros"
                                                },
                                                {
                                                                "name": "Vida Escolar",
                                                                "value": "vida-escolar"
                                                },
                                                {
                                                                "name": "0",
                                                                "value": "0"
                                                },
                                                {
                                                                "name": "1",
                                                                "value": "1"
                                                },
                                                {
                                                                "name": "2",
                                                                "value": "2"
                                                },
                                                {
                                                                "name": "3",
                                                                "value": "3"
                                                },
                                                {
                                                                "name": "4",
                                                                "value": "4"
                                                },
                                                {
                                                                "name": "5",
                                                                "value": "5"
                                                },
                                                {
                                                                "name": "6",
                                                                "value": "6"
                                                },
                                                {
                                                                "name": "7",
                                                                "value": "7"
                                                },
                                                {
                                                                "name": "8",
                                                                "value": "8"
                                                },
                                                {
                                                                "name": "9",
                                                                "value": "9"
                                                },
                                                {
                                                                "name": "A",
                                                                "value": "A"
                                                },
                                                {
                                                                "name": "B",
                                                                "value": "B"
                                                },
                                                {
                                                                "name": "C",
                                                                "value": "C"
                                                },
                                                {
                                                                "name": "D",
                                                                "value": "D"
                                                },
                                                {
                                                                "name": "E",
                                                                "value": "E"
                                                },
                                                {
                                                                "name": "F",
                                                                "value": "F"
                                                },
                                                {
                                                                "name": "G",
                                                                "value": "G"
                                                },
                                                {
                                                                "name": "H",
                                                                "value": "H"
                                                },
                                                {
                                                                "name": "I",
                                                                "value": "I"
                                                },
                                                {
                                                                "name": "J",
                                                                "value": "J"
                                                },
                                                {
                                                                "name": "K",
                                                                "value": "K"
                                                },
                                                {
                                                                "name": "L",
                                                                "value": "L"
                                                },
                                                {
                                                                "name": "M",
                                                                "value": "M"
                                                },
                                                {
                                                                "name": "N",
                                                                "value": "N"
                                                },
                                                {
                                                                "name": "O",
                                                                "value": "O"
                                                },
                                                {
                                                                "name": "P",
                                                                "value": "P"
                                                },
                                                {
                                                                "name": "Q",
                                                                "value": "Q"
                                                },
                                                {
                                                                "name": "R",
                                                                "value": "R"
                                                },
                                                {
                                                                "name": "S",
                                                                "value": "S"
                                                },
                                                {
                                                                "name": "T",
                                                                "value": "T"
                                                },
                                                {
                                                                "name": "U",
                                                                "value": "U"
                                                },
                                                {
                                                                "name": "V",
                                                                "value": "V"
                                                },
                                                {
                                                                "name": "W",
                                                                "value": "W"
                                                },
                                                {
                                                                "name": "X",
                                                                "value": "X"
                                                },
                                                {
                                                                "name": "Y",
                                                                "value": "Y"
                                                },
                                                {
                                                                "name": "Z",
                                                                "value": "Z"
                                                },
                                                {
                                                                "name": "Completed",
                                                                "value": "completed"
                                                },
                                                {
                                                                "name": "Ongoing",
                                                                "value": "ongoing"
                                                },
                                                {
                                                                "name": "Paused",
                                                                "value": "paused"
                                                },
                                                {
                                                                "name": "Cancelled",
                                                                "value": "cancelled"
                                                }
                                ],
                                "default": "accion"
                }
]
        return [SourceFilter(**item) for item in data]

    name = 'leercapitulo_es'
    display_name = 'LeerCapitulo'
    base_url = 'https://www.leercapitulo.co'
    language = 'es'
    requests_per_minute = 60


SOURCE = LeercapituloSource
