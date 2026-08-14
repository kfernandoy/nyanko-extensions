try:
    from .madara import MadaraSource, _Node, _TreeParser
except ImportError:
    pass

class MadaraSource:
    pass


class HeavenmangaSource(MadaraSource):
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
                                                                "name": "Accion",
                                                                "value": "accion"
                                                },
                                                {
                                                                "name": "Adulto",
                                                                "value": "adulto"
                                                },
                                                {
                                                                "name": "Artes Marciales",
                                                                "value": "artes-marciales"
                                                },
                                                {
                                                                "name": "Acontesimientos de la Vida",
                                                                "value": "acontesimientos-de-la-vida"
                                                },
                                                {
                                                                "name": "Bakunyuu",
                                                                "value": "bakunyuu"
                                                },
                                                {
                                                                "name": "Gore",
                                                                "value": "gore"
                                                },
                                                {
                                                                "name": "Gender Bender",
                                                                "value": "gender-bender"
                                                },
                                                {
                                                                "name": "Humor",
                                                                "value": "humor"
                                                },
                                                {
                                                                "name": "Harem",
                                                                "value": "harem"
                                                },
                                                {
                                                                "name": "Hentai",
                                                                "value": "hentai"
                                                },
                                                {
                                                                "name": "Horror",
                                                                "value": "horror"
                                                },
                                                {
                                                                "name": "Historico",
                                                                "value": "historico"
                                                },
                                                {
                                                                "name": "Josei",
                                                                "value": "josei"
                                                },
                                                {
                                                                "name": "Loli",
                                                                "value": "loli"
                                                },
                                                {
                                                                "name": "Light",
                                                                "value": "light"
                                                },
                                                {
                                                                "name": "Lucha Libre",
                                                                "value": "lucha-libre"
                                                },
                                                {
                                                                "name": "Manga",
                                                                "value": "manga"
                                                },
                                                {
                                                                "name": "Mecha",
                                                                "value": "mecha"
                                                },
                                                {
                                                                "name": "Magia",
                                                                "value": "magia"
                                                },
                                                {
                                                                "name": "Manhwa",
                                                                "value": "manhwa"
                                                },
                                                {
                                                                "name": "Manhua",
                                                                "value": "manhua"
                                                },
                                                {
                                                                "name": "Mature",
                                                                "value": "mature"
                                                },
                                                {
                                                                "name": "Misterio",
                                                                "value": "misterio"
                                                },
                                                {
                                                                "name": "Mutantes",
                                                                "value": "mutantes"
                                                },
                                                {
                                                                "name": "Novela",
                                                                "value": "novela"
                                                },
                                                {
                                                                "name": "OneShot",
                                                                "value": "oneshot"
                                                },
                                                {
                                                                "name": "Psicologico",
                                                                "value": "psicologico"
                                                },
                                                {
                                                                "name": "Romance",
                                                                "value": "romance"
                                                },
                                                {
                                                                "name": "Recuentos de la vida",
                                                                "value": "recuentos-de-la-vida"
                                                },
                                                {
                                                                "name": "Smut",
                                                                "value": "smut"
                                                },
                                                {
                                                                "name": "Shojo",
                                                                "value": "shojo"
                                                },
                                                {
                                                                "name": "Shonen",
                                                                "value": "shonen"
                                                },
                                                {
                                                                "name": "Seinen",
                                                                "value": "seinen"
                                                },
                                                {
                                                                "name": "Shoujo",
                                                                "value": "shoujo"
                                                },
                                                {
                                                                "name": "Shounen",
                                                                "value": "shounen"
                                                },
                                                {
                                                                "name": "Suspenso",
                                                                "value": "suspenso"
                                                },
                                                {
                                                                "name": "School Life",
                                                                "value": "school-life"
                                                },
                                                {
                                                                "name": "SuperHeroes",
                                                                "value": "superheroes"
                                                },
                                                {
                                                                "name": "Supernatural",
                                                                "value": "supernatural"
                                                },
                                                {
                                                                "name": "Slice of Life",
                                                                "value": "slice-of-life"
                                                },
                                                {
                                                                "name": "Super Poderes",
                                                                "value": "super-poderes"
                                                },
                                                {
                                                                "name": "Torneo",
                                                                "value": "torneo"
                                                },
                                                {
                                                                "name": "Tragedia",
                                                                "value": "tragedia"
                                                },
                                                {
                                                                "name": "Transexual",
                                                                "value": "transexual"
                                                },
                                                {
                                                                "name": "Vampiros",
                                                                "value": "vampiros"
                                                },
                                                {
                                                                "name": "Violencia",
                                                                "value": "violencia"
                                                },
                                                {
                                                                "name": "Vida Pasadas",
                                                                "value": "vida-pasadas"
                                                },
                                                {
                                                                "name": "Vida Cotidiana",
                                                                "value": "vida-cotidiana"
                                                },
                                                {
                                                                "name": "Vida de Escuela",
                                                                "value": "vida-de-escuela"
                                                },
                                                {
                                                                "name": "Webtoon",
                                                                "value": "webtoon"
                                                },
                                                {
                                                                "name": "Yaoi",
                                                                "value": "yaoi"
                                                },
                                                {
                                                                "name": "Yuri",
                                                                "value": "yuri"
                                                },
                                                {
                                                                "name": "Sobrenatural",
                                                                "value": "sobrenatural"
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
                                                                "name": "Comedia",
                                                                "value": "comedia"
                                                },
                                                {
                                                                "name": "Aventura",
                                                                "value": "aventura"
                                                },
                                                {
                                                                "name": "Fantasia",
                                                                "value": "fantasia"
                                                },
                                                {
                                                                "name": "Demonios",
                                                                "value": "demonios"
                                                },
                                                {
                                                                "name": "Superpoderes",
                                                                "value": "superpoderes"
                                                },
                                                {
                                                                "name": "Deporte",
                                                                "value": "deporte"
                                                },
                                                {
                                                                "name": "Ciencia Ficcion",
                                                                "value": "ciencia-ficcion"
                                                },
                                                {
                                                                "name": "Supervivencia",
                                                                "value": "supervivencia"
                                                },
                                                {
                                                                "name": "Crimen",
                                                                "value": "crimen"
                                                },
                                                {
                                                                "name": "Reencarnación",
                                                                "value": "reencarnacion"
                                                },
                                                {
                                                                "name": "Género Bender",
                                                                "value": "genero-bender"
                                                },
                                                {
                                                                "name": "Apocaliptico",
                                                                "value": "apocaliptico"
                                                },
                                                {
                                                                "name": "Familia",
                                                                "value": "familia"
                                                },
                                                {
                                                                "name": "Militar",
                                                                "value": "militar"
                                                },
                                                {
                                                                "name": "Guerra",
                                                                "value": "guerra"
                                                },
                                                {
                                                                "name": "Realidad",
                                                                "value": "realidad"
                                                },
                                                {
                                                                "name": "Animación",
                                                                "value": "animacion"
                                                },
                                                {
                                                                "name": "Musica",
                                                                "value": "musica"
                                                },
                                                {
                                                                "name": "Samurái",
                                                                "value": "samurai"
                                                },
                                                {
                                                                "name": "Historia",
                                                                "value": "historia"
                                                },
                                                {
                                                                "name": "Thriller",
                                                                "value": "thriller"
                                                },
                                                {
                                                                "name": "Girls Love",
                                                                "value": "girls-love"
                                                },
                                                {
                                                                "name": "Zombies",
                                                                "value": "zombies"
                                                },
                                                {
                                                                "name": "Netorare",
                                                                "value": "netorare"
                                                },
                                                {
                                                                "name": "Boys Love",
                                                                "value": "boys-love"
                                                },
                                                {
                                                                "name": "Transmigración",
                                                                "value": "transmigracion"
                                                },
                                                {
                                                                "name": "Regresion",
                                                                "value": "regresion"
                                                },
                                                {
                                                                "name": "Harem Inverso",
                                                                "value": "harem-inverso"
                                                },
                                                {
                                                                "name": "Moderno",
                                                                "value": "moderno"
                                                },
                                                {
                                                                "name": "Sistema",
                                                                "value": "sistema"
                                                },
                                                {
                                                                "name": "Venganza",
                                                                "value": "venganza"
                                                },
                                                {
                                                                "name": "Amateur",
                                                                "value": "amateur"
                                                },
                                                {
                                                                "name": "Parodia",
                                                                "value": "parodia"
                                                },
                                                {
                                                                "name": "Matrimonio",
                                                                "value": "matrimonio"
                                                },
                                                {
                                                                "name": "Other",
                                                                "value": "Other"
                                                },
                                                {
                                                                "name": "A",
                                                                "value": "a"
                                                },
                                                {
                                                                "name": "B",
                                                                "value": "b"
                                                },
                                                {
                                                                "name": "C",
                                                                "value": "c"
                                                },
                                                {
                                                                "name": "D",
                                                                "value": "d"
                                                },
                                                {
                                                                "name": "E",
                                                                "value": "e"
                                                },
                                                {
                                                                "name": "F",
                                                                "value": "f"
                                                },
                                                {
                                                                "name": "G",
                                                                "value": "g"
                                                },
                                                {
                                                                "name": "H",
                                                                "value": "h"
                                                },
                                                {
                                                                "name": "I",
                                                                "value": "i"
                                                },
                                                {
                                                                "name": "J",
                                                                "value": "j"
                                                },
                                                {
                                                                "name": "K",
                                                                "value": "k"
                                                },
                                                {
                                                                "name": "L",
                                                                "value": "l"
                                                },
                                                {
                                                                "name": "M",
                                                                "value": "m"
                                                },
                                                {
                                                                "name": "N",
                                                                "value": "n"
                                                },
                                                {
                                                                "name": "O",
                                                                "value": "o"
                                                },
                                                {
                                                                "name": "P",
                                                                "value": "p"
                                                },
                                                {
                                                                "name": "Q",
                                                                "value": "q"
                                                },
                                                {
                                                                "name": "R",
                                                                "value": "r"
                                                },
                                                {
                                                                "name": "S",
                                                                "value": "s"
                                                },
                                                {
                                                                "name": "T",
                                                                "value": "t"
                                                },
                                                {
                                                                "name": "U",
                                                                "value": "u"
                                                },
                                                {
                                                                "name": "V",
                                                                "value": "v"
                                                },
                                                {
                                                                "name": "W",
                                                                "value": "w"
                                                },
                                                {
                                                                "name": "X",
                                                                "value": "x"
                                                },
                                                {
                                                                "name": "Y",
                                                                "value": "y"
                                                },
                                                {
                                                                "name": "Z",
                                                                "value": "z"
                                                },
                                                {
                                                                "name": "0-9",
                                                                "value": "0-9"
                                                },
                                                {
                                                                "name": "Lista Comis",
                                                                "value": "comic"
                                                },
                                                {
                                                                "name": "Lista Novelas",
                                                                "value": "novela"
                                                },
                                                {
                                                                "name": "Lista Adulto",
                                                                "value": "adulto"
                                                }
                                ],
                                "default": "accion"
                }
]
        return [SourceFilter(**item) for item in data]

    name = 'heavenmanga_es'
    display_name = 'HeavenManga'
    base_url = 'https://heavenmanga.com'
    language = 'es'
    requests_per_minute = 60


SOURCE = HeavenmangaSource
