try:
    from .madara import MadaraSource, _Node, _TreeParser
except ImportError:
    pass

class MadaraSource:
    pass


class MangafireSource(MadaraSource):
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
                                                                "name": "Manga",
                                                                "value": "manga"
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
                                                                "name": "Other",
                                                                "value": "other"
                                                },
                                                {
                                                                "name": "Action",
                                                                "value": "1"
                                                },
                                                {
                                                                "name": "Adult",
                                                                "value": "268929"
                                                },
                                                {
                                                                "name": "Adventure",
                                                                "value": "78"
                                                },
                                                {
                                                                "name": "Avant Garde",
                                                                "value": "3"
                                                },
                                                {
                                                                "name": "Boys Love",
                                                                "value": "4"
                                                },
                                                {
                                                                "name": "Comedy",
                                                                "value": "5"
                                                },
                                                {
                                                                "name": "Crime",
                                                                "value": "268921"
                                                },
                                                {
                                                                "name": "Demons",
                                                                "value": "77"
                                                },
                                                {
                                                                "name": "Drama",
                                                                "value": "6"
                                                },
                                                {
                                                                "name": "Ecchi",
                                                                "value": "7"
                                                },
                                                {
                                                                "name": "Fantasy",
                                                                "value": "79"
                                                },
                                                {
                                                                "name": "Girls Love",
                                                                "value": "9"
                                                },
                                                {
                                                                "name": "Gourmet",
                                                                "value": "10"
                                                },
                                                {
                                                                "name": "Harem",
                                                                "value": "11"
                                                },
                                                {
                                                                "name": "Hentai",
                                                                "value": "268930"
                                                },
                                                {
                                                                "name": "Historical",
                                                                "value": "268922"
                                                },
                                                {
                                                                "name": "Horror",
                                                                "value": "530"
                                                },
                                                {
                                                                "name": "Isekai",
                                                                "value": "13"
                                                },
                                                {
                                                                "name": "Iyashikei",
                                                                "value": "531"
                                                },
                                                {
                                                                "name": "Josei",
                                                                "value": "15"
                                                },
                                                {
                                                                "name": "Kids",
                                                                "value": "532"
                                                },
                                                {
                                                                "name": "Magic",
                                                                "value": "539"
                                                },
                                                {
                                                                "name": "Magical Girls",
                                                                "value": "268923"
                                                },
                                                {
                                                                "name": "Mahou Shoujo",
                                                                "value": "533"
                                                },
                                                {
                                                                "name": "Martial Arts",
                                                                "value": "534"
                                                },
                                                {
                                                                "name": "Mature",
                                                                "value": "268931"
                                                },
                                                {
                                                                "name": "Mecha",
                                                                "value": "19"
                                                },
                                                {
                                                                "name": "Medical",
                                                                "value": "268924"
                                                },
                                                {
                                                                "name": "Military",
                                                                "value": "535"
                                                },
                                                {
                                                                "name": "Music",
                                                                "value": "21"
                                                },
                                                {
                                                                "name": "Mystery",
                                                                "value": "22"
                                                },
                                                {
                                                                "name": "Parody",
                                                                "value": "23"
                                                },
                                                {
                                                                "name": "Philosophical",
                                                                "value": "268925"
                                                },
                                                {
                                                                "name": "Psychological",
                                                                "value": "536"
                                                },
                                                {
                                                                "name": "Reverse Harem",
                                                                "value": "25"
                                                },
                                                {
                                                                "name": "Romance",
                                                                "value": "26"
                                                },
                                                {
                                                                "name": "School",
                                                                "value": "73"
                                                },
                                                {
                                                                "name": "Sci-Fi",
                                                                "value": "28"
                                                },
                                                {
                                                                "name": "Seinen",
                                                                "value": "537"
                                                },
                                                {
                                                                "name": "Shoujo",
                                                                "value": "30"
                                                },
                                                {
                                                                "name": "Shounen",
                                                                "value": "31"
                                                },
                                                {
                                                                "name": "Slice of Life",
                                                                "value": "538"
                                                },
                                                {
                                                                "name": "Smut",
                                                                "value": "268932"
                                                },
                                                {
                                                                "name": "Space",
                                                                "value": "33"
                                                },
                                                {
                                                                "name": "Sports",
                                                                "value": "34"
                                                },
                                                {
                                                                "name": "Super Power",
                                                                "value": "75"
                                                },
                                                {
                                                                "name": "Superhero",
                                                                "value": "268926"
                                                },
                                                {
                                                                "name": "Supernatural",
                                                                "value": "76"
                                                },
                                                {
                                                                "name": "Suspense",
                                                                "value": "37"
                                                },
                                                {
                                                                "name": "Thriller",
                                                                "value": "38"
                                                },
                                                {
                                                                "name": "Tragedy",
                                                                "value": "268927"
                                                },
                                                {
                                                                "name": "Vampire",
                                                                "value": "39"
                                                },
                                                {
                                                                "name": "Wuxia",
                                                                "value": "268928"
                                                },
                                                {
                                                                "name": "Aliens",
                                                                "value": "268933"
                                                },
                                                {
                                                                "name": "Animals",
                                                                "value": "268934"
                                                },
                                                {
                                                                "name": "Cooking",
                                                                "value": "268935"
                                                },
                                                {
                                                                "name": "Crossdressing",
                                                                "value": "268936"
                                                },
                                                {
                                                                "name": "Delinquents",
                                                                "value": "268937"
                                                },
                                                {
                                                                "name": "Genderswap",
                                                                "value": "268939"
                                                },
                                                {
                                                                "name": "Ghosts",
                                                                "value": "268940"
                                                },
                                                {
                                                                "name": "Gyaru",
                                                                "value": "268941"
                                                },
                                                {
                                                                "name": "Incest",
                                                                "value": "268943"
                                                },
                                                {
                                                                "name": "Loli",
                                                                "value": "268944"
                                                },
                                                {
                                                                "name": "Mafia",
                                                                "value": "268945"
                                                },
                                                {
                                                                "name": "Monster Girls",
                                                                "value": "268949"
                                                },
                                                {
                                                                "name": "Monsters",
                                                                "value": "268950"
                                                },
                                                {
                                                                "name": "Ninja",
                                                                "value": "268952"
                                                },
                                                {
                                                                "name": "Office Workers",
                                                                "value": "268953"
                                                },
                                                {
                                                                "name": "Police",
                                                                "value": "268954"
                                                },
                                                {
                                                                "name": "Post-Apocalyptic",
                                                                "value": "268955"
                                                },
                                                {
                                                                "name": "Reincarnation",
                                                                "value": "268956"
                                                },
                                                {
                                                                "name": "Samurai",
                                                                "value": "268958"
                                                },
                                                {
                                                                "name": "School Life",
                                                                "value": "268959"
                                                },
                                                {
                                                                "name": "Shota",
                                                                "value": "268960"
                                                },
                                                {
                                                                "name": "Survival",
                                                                "value": "268962"
                                                },
                                                {
                                                                "name": "Time Travel",
                                                                "value": "268963"
                                                },
                                                {
                                                                "name": "Traditional Games",
                                                                "value": "268964"
                                                },
                                                {
                                                                "name": "Vampires",
                                                                "value": "268965"
                                                },
                                                {
                                                                "name": "Video Games",
                                                                "value": "268966"
                                                },
                                                {
                                                                "name": "Villainess",
                                                                "value": "268967"
                                                },
                                                {
                                                                "name": "Virtual Reality",
                                                                "value": "268968"
                                                },
                                                {
                                                                "name": "Zombies",
                                                                "value": "268969"
                                                },
                                                {
                                                                "name": "Releasing",
                                                                "value": "releasing"
                                                },
                                                {
                                                                "name": "Finished",
                                                                "value": "finished"
                                                },
                                                {
                                                                "name": "On Hiatus",
                                                                "value": "on_hiatus"
                                                },
                                                {
                                                                "name": "Discontinued",
                                                                "value": "discontinued"
                                                },
                                                {
                                                                "name": "Not Yet Released",
                                                                "value": "not_yet_released"
                                                }
                                ],
                                "default": "manga"
                }
]
        return [SourceFilter(**item) for item in data]

    name = 'mangafire_es'
    display_name = 'MangaFire'
    base_url = 'https://mangafire.to'
    language = 'es'
    requests_per_minute = 120


SOURCE = MangafireSource
