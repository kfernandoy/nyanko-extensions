try:

    from .base import FuenteBaseSource, _Node, _TreeParser

except ImportError:

    pass



class FuenteBaseSource:

    pass





class AnzmangaSource(FuenteBaseSource):

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

                                                                "value": "1"

                                                },

                                                {

                                                                "name": "Aventura",

                                                                "value": "2"

                                                },

                                                {

                                                                "name": "Comedia",

                                                                "value": "3"

                                                },

                                                {

                                                                "name": "Doujinshi",

                                                                "value": "4"

                                                },

                                                {

                                                                "name": "Drama",

                                                                "value": "5"

                                                },

                                                {

                                                                "name": "Ecchi",

                                                                "value": "6"

                                                },

                                                {

                                                                "name": "Fantasía",

                                                                "value": "7"

                                                },

                                                {

                                                                "name": "Gender Bender",

                                                                "value": "8"

                                                },

                                                {

                                                                "name": "Harem",

                                                                "value": "9"

                                                },

                                                {

                                                                "name": "Histórico",

                                                                "value": "10"

                                                },

                                                {

                                                                "name": "Horror",

                                                                "value": "11"

                                                },

                                                {

                                                                "name": "Josei",

                                                                "value": "12"

                                                },

                                                {

                                                                "name": "Artes Marciales",

                                                                "value": "13"

                                                },

                                                {

                                                                "name": "Mature",

                                                                "value": "14"

                                                },

                                                {

                                                                "name": "Mecha",

                                                                "value": "15"

                                                },

                                                {

                                                                "name": "Misterio",

                                                                "value": "16"

                                                },

                                                {

                                                                "name": "One Shot",

                                                                "value": "17"

                                                },

                                                {

                                                                "name": "Psicológico",

                                                                "value": "18"

                                                },

                                                {

                                                                "name": "Romance",

                                                                "value": "19"

                                                },

                                                {

                                                                "name": "Escolares",

                                                                "value": "20"

                                                },

                                                {

                                                                "name": "Ciencia Ficción",

                                                                "value": "21"

                                                },

                                                {

                                                                "name": "Seinen",

                                                                "value": "22"

                                                },

                                                {

                                                                "name": "Shoujo",

                                                                "value": "23"

                                                },

                                                {

                                                                "name": "Shoujo Ai",

                                                                "value": "24"

                                                },

                                                {

                                                                "name": "Shounen",

                                                                "value": "25"

                                                },

                                                {

                                                                "name": "Shounen Ai",

                                                                "value": "26"

                                                },

                                                {

                                                                "name": "Recuentos de la vida",

                                                                "value": "27"

                                                },

                                                {

                                                                "name": "Deportes",

                                                                "value": "28"

                                                },

                                                {

                                                                "name": "Sobrenatural",

                                                                "value": "29"

                                                },

                                                {

                                                                "name": "Tragedia",

                                                                "value": "30"

                                                },

                                                {

                                                                "name": "Yaoi",

                                                                "value": "31"

                                                },

                                                {

                                                                "name": "Yuri",

                                                                "value": "32"

                                                },

                                                {

                                                                "name": "Magia",

                                                                "value": "33"

                                                },

                                                {

                                                                "name": "Gore",

                                                                "value": "34"

                                                },

                                                {

                                                                "name": "Manhwa",

                                                                "value": "35"

                                                },

                                                {

                                                                "name": "Manhua",

                                                                "value": "36"

                                                },

                                                {

                                                                "name": "Música",

                                                                "value": "37"

                                                },

                                                {

                                                                "name": "AZ",

                                                                "value": "name"

                                                },

                                                {

                                                                "name": "Visitas",

                                                                "value": "views"

                                                }

                                ],

                                "default": "1"

                }

]

        return [SourceFilter(**item) for item in data]



    name = 'anzmanga_es'

    display_name = 'AnzManga'

    base_url = 'https://www.anzmanga25.com'

    language = 'es'

    requests_per_minute = 60





class AnzMangaSource(GeneratedGenericSource):

    def get_filters(self) -> list[SourceFilter]:

        categories = [

            ("", "Todas"), ("1", "Acción"), ("2", "Aventura"), ("3", "Comedia"),

            ("4", "Doujinshi"), ("5", "Drama"), ("6", "Ecchi"), ("7", "Fantasía"),

            ("8", "Gender Bender"), ("9", "Harem"), ("10", "Histórico"), ("11", "Horror"),

            ("12", "Josei"), ("13", "Artes Marciales"), ("14", "Mature"), ("15", "Mecha"),

            ("16", "Misterio"), ("17", "One Shot"), ("18", "Psicológico"), ("19", "Romance"),

            ("20", "Escolares"), ("21", "Ciencia Ficción"), ("22", "Seinen"), ("23", "Shoujo"),

            ("24", "Shoujo Ai"), ("25", "Shounen"), ("26", "Shounen Ai"),

            ("27", "Recuentos de la vida"), ("28", "Deportes"), ("29", "Sobrenatural"),

            ("30", "Tragedia"), ("31", "Yaoi"), ("32", "Yuri"), ("33", "Magia"),

            ("34", "Gore"), ("35", "Manhwa"), ("36", "Manhua"), ("37", "Música"),

        ]

        return [

            SourceFilter("category", "Categoría", "select", categories, ""),

            SourceFilter("sort_by", "Ordenar por", "select", [("name", "AZ"), ("views", "Visitas")], "views"),

            SourceFilter("ascending", "Ascendente", "checkbox", default=False),

        ]



    @staticmethod

    def _next(root) -> bool:

        return any(

            node.tag == "a" and "next" in node.attrs.get("rel", "").split()

            for node in root.descendants("a")

        )



    @staticmethod

    def _has_id_ancestor(node, value: str) -> bool:

        parent = node.parent

        while parent is not None:

            if parent.attrs.get("id") == value:

                return True

            parent = parent.parent

        return False



    def _listing(self, response, latest: bool = False) -> dict:

        root = _parse_html(response.text)

        class_name = "manga-item" if latest else "media"

        items = []

        for container in root.descendants("div"):

            if not container.has_class(class_name):

                continue

            heading = "manga-heading" if latest else "media-heading"

            anchor = _first(

                container,

                lambda node: node.tag == "a"

                and bool(node.attrs.get("href"))

                and self._has_class_ancestor(node, heading),

            )

            if anchor is None:

                continue

            source_id = urljoin(str(response.url), anchor.attrs["href"])

            image = _first(container, lambda node: node.tag == "img")

            cover = _image_url(image, str(response.url)) if image else ""

            if latest:

                slug = source_id.rstrip("/").rsplit("/", 1)[-1]

                cover = f"{self.base_url}/uploads/manga/{slug}/cover/cover_250x350.jpg"

            items.append(SourceSeries(

                source_id=source_id,

                title=anchor.text().strip(),

                source_name=self.name,

                cover_url=cover or None,

                web_url=source_id,

            ))

        return {"items": items, "has_more": self._next(root)}



    async def browse(self, kind: str, page: int = 1):

        if kind == "popular":

            response = await self._request(

                "GET", f"{self.base_url}/filterList",

                params={"page": str(page), "sortBy": "views", "asc": "false"},

            )

            latest = False

        elif kind == "latest":

            response = await self._request("GET", f"{self.base_url}/latest-release", params={"page": str(page)})

            latest = True

        else:

            return {"items": [], "has_more": False}

        response.raise_for_status()

        return self._listing(response, latest)



    async def search(self, query: str, page: int = 1, filters: dict | None = None):

        filters = filters or {}

        if query.strip():

            response = await self._request("GET", f"{self.base_url}/search", params={"query": query.strip()})

            response.raise_for_status()

            try:

                payload = response.json()

            except (AttributeError, ValueError):

                payload = json.loads(response.text)

            items = []

            for suggestion in payload.get("suggestions", []):

                slug = suggestion.get("data", "")

                if not slug:

                    continue

                source_id = f"{self.base_url}/manga/{slug}"

                items.append(SourceSeries(

                    source_id=source_id,

                    title=str(suggestion.get("value", "")),

                    source_name=self.name,

                    cover_url=f"{self.base_url}/uploads/manga/{slug}/cover/cover_250x350.jpg",

                    web_url=source_id,

                ))

            return {"items": items, "has_more": False}

        response = await self._request(

            "GET", f"{self.base_url}/filterList",

            params={

                "page": str(page), "cat": filters.get("category", ""), "alpha": "",

                "sortBy": filters.get("sort_by", "views"),

                "asc": str(bool(filters.get("ascending", False))).lower(),

                "author": "", "artist": "", "tag": "",

            },

        )

        response.raise_for_status()

        return self._listing(response)



    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:

        series_id = series.source_id if isinstance(series, SourceSeries) else series

        response = await self._request("GET", series_id)

        response.raise_for_status()

        root = _parse_html(response.text)

        result = []

        for item in root.descendants("li"):

            if not self._has_class_ancestor(item, "chapters"):

                continue

            anchor = _first(item, lambda node: node.tag == "a" and bool(node.attrs.get("href")))

            if anchor is None:

                continue

            extra = _first(item, lambda node: node.tag == "em")

            title = anchor.text().strip() + (f" : {extra.text().strip()}" if extra else "")

            number = re.search(r"\d+(?:\.\d+)?", title)

            result.append(SourceChapter(

                source_id=urljoin(str(response.url), anchor.attrs["href"]),

                title=title,

                series_id=series_id,

                source_name=self.name,

                number=float(number.group()) if number else None,

                language=self.language,

            ))

        return result



    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:

        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter

        response = await self._request("GET", chapter_id)

        response.raise_for_status()

        root = _parse_html(response.text)

        urls = [

            _image_url(image, str(response.url))

            for image in root.descendants("img")

            if image.has_class("img-responsive")

            and self._has_id_ancestor(image, "all")

        ]

        return [SourcePage(

            source_id=url,

            chapter_id=chapter_id,

            index=index,

            filename=url.rsplit("/", 1)[-1].split("?", 1)[0] or f"{index}.jpg",

            source_name=self.name,

        ) for index, url in enumerate(urls)]





SOURCE = AnzMangaSource