try:

    from .base import FuenteBaseSource, _Node, _TreeParser

except ImportError:

    pass



class FuenteBaseSource:

    pass





class CatmanhwasSource(FuenteBaseSource):

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

                                                                "name": "+19",

                                                                "value": "1"

                                                },

                                                {

                                                                "name": "Acción",

                                                                "value": "2"

                                                },

                                                {

                                                                "name": "Adulto",

                                                                "value": "3"

                                                },

                                                {

                                                                "name": "Apocalíptico",

                                                                "value": "4"

                                                },

                                                {

                                                                "name": "Aventura",

                                                                "value": "5"

                                                },

                                                {

                                                                "name": "BDSM",

                                                                "value": "6"

                                                },

                                                {

                                                                "name": "BL",

                                                                "value": "7"

                                                },

                                                {

                                                                "name": "Ciencia Ficción",

                                                                "value": "8"

                                                },

                                                {

                                                                "name": "Comedia",

                                                                "value": "9"

                                                },

                                                {

                                                                "name": "Crimen",

                                                                "value": "10"

                                                },

                                                {

                                                                "name": "Demonios",

                                                                "value": "11"

                                                },

                                                {

                                                                "name": "Deportes",

                                                                "value": "12"

                                                },

                                                {

                                                                "name": "Descensurado",

                                                                "value": "13"

                                                },

                                                {

                                                                "name": "Drama",

                                                                "value": "14"

                                                },

                                                {

                                                                "name": "Ecchi",

                                                                "value": "15"

                                                },

                                                {

                                                                "name": "Familia",

                                                                "value": "16"

                                                },

                                                {

                                                                "name": "Fantasía",

                                                                "value": "17"

                                                },

                                                {

                                                                "name": "Gender Bender",

                                                                "value": "18"

                                                },

                                                {

                                                                "name": "GL",

                                                                "value": "19"

                                                },

                                                {

                                                                "name": "Gogogo",

                                                                "value": "20"

                                                },

                                                {

                                                                "name": "Harem",

                                                                "value": "21"

                                                },

                                                {

                                                                "name": "Histórico",

                                                                "value": "22"

                                                },

                                                {

                                                                "name": "Horror",

                                                                "value": "23"

                                                },

                                                {

                                                                "name": "Isekai",

                                                                "value": "24"

                                                },

                                                {

                                                                "name": "Josei",

                                                                "value": "25"

                                                },

                                                {

                                                                "name": "Magia",

                                                                "value": "26"

                                                },

                                                {

                                                                "name": "Mazmorras",

                                                                "value": "27"

                                                },

                                                {

                                                                "name": "Militar",

                                                                "value": "28"

                                                },

                                                {

                                                                "name": "Misterio",

                                                                "value": "29"

                                                },

                                                {

                                                                "name": "Omegaverse",

                                                                "value": "30"

                                                },

                                                {

                                                                "name": "Psicológico",

                                                                "value": "31"

                                                },

                                                {

                                                                "name": "Reencarnación",

                                                                "value": "32"

                                                },

                                                {

                                                                "name": "Regresión",

                                                                "value": "33"

                                                },

                                                {

                                                                "name": "Romance",

                                                                "value": "34"

                                                },

                                                {

                                                                "name": "Seinen",

                                                                "value": "35"

                                                },

                                                {

                                                                "name": "Shoujo",

                                                                "value": "36"

                                                },

                                                {

                                                                "name": "Shounen",

                                                                "value": "37"

                                                },

                                                {

                                                                "name": "Sistemas",

                                                                "value": "38"

                                                },

                                                {

                                                                "name": "Smut",

                                                                "value": "39"

                                                },

                                                {

                                                                "name": "Sobrenatural",

                                                                "value": "40"

                                                },

                                                {

                                                                "name": "Soft BL",

                                                                "value": "41"

                                                },

                                                {

                                                                "name": "Supervivencia",

                                                                "value": "42"

                                                },

                                                {

                                                                "name": "Terror Psicológico",

                                                                "value": "43"

                                                },

                                                {

                                                                "name": "Thriller",

                                                                "value": "44"

                                                },

                                                {

                                                                "name": "Tragedia",

                                                                "value": "45"

                                                },

                                                {

                                                                "name": "Trasmigración",

                                                                "value": "46"

                                                },

                                                {

                                                                "name": "Vampiros",

                                                                "value": "47"

                                                },

                                                {

                                                                "name": "Venganza",

                                                                "value": "48"

                                                },

                                                {

                                                                "name": "Vida cotidiana",

                                                                "value": "49"

                                                },

                                                {

                                                                "name": "Vida escolar",

                                                                "value": "50"

                                                },

                                                {

                                                                "name": "Videojuegos",

                                                                "value": "51"

                                                },

                                                {

                                                                "name": "Wuxia",

                                                                "value": "52"

                                                },

                                                {

                                                                "name": "Alfabético A-Z",

                                                                "value": "name"

                                                },

                                                {

                                                                "name": "Más recientes",

                                                                "value": "recent"

                                                },

                                                {

                                                                "name": "Más populares",

                                                                "value": "popular"

                                                },

                                                {

                                                                "name": "Mejor calificados",

                                                                "value": "rating"

                                                }

                                ],

                                "default": "1"

                }

]

        return [SourceFilter(**item) for item in data]



    name = 'catmanhwas_es'

    display_name = 'Catoons'

    base_url = 'https://newcat1.xyz'

    language = 'es'

    requests_per_minute = 180





class CatManhwasSource(GeneratedGenericSource):

    _details_chunk = None

    _chapters_chunk = None



    def get_filters(self) -> list[SourceFilter]:

        raw = super().get_filters()

        options = raw[0].options if raw else []

        pairs = [

            (str(option.get("value", "")), str(option.get("name", "")))

            for option in options or [] if isinstance(option, dict)

        ]

        orders = {"name", "recent", "popular", "rating"}

        return [

            SourceFilter("genre", "Género", "select", [("", "Todos")] + [pair for pair in pairs if pair[0] not in orders], ""),

            SourceFilter("sort", "Ordenar por", "select", [pair for pair in pairs if pair[0] in orders], "name"),

        ]



    @classmethod

    def _decode_svelte(cls, data):

        def dereference(index):

            value = data[index]

            return resolve(value) if isinstance(value, (list, dict)) else value



        def reference(value):

            if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < len(data):

                return dereference(value)

            return resolve(value)



        def resolve(value):

            if isinstance(value, list):

                return [reference(item) for item in value]

            if isinstance(value, dict):

                return {key: reference(item) for key, item in value.items()}

            return value

        return resolve(data[0])



    @staticmethod

    def _payload(response):

        try:

            return response.json()

        except (AttributeError, ValueError):

            return json.loads(response.text)



    def _browse_result(self, response) -> dict:

        payload = self._payload(response)

        node = next(node for node in payload.get("nodes", []) if node.get("type") == "data")

        data = self._decode_svelte(node["data"])

        items = []

        for value in data.get("series", []):

            slug = str(value.get("slug", ""))

            if not slug:

                continue

            items.append(SourceSeries(

                source_id=slug,

                title=str(value.get("name", "")),

                source_name=self.name,

                cover_url=value.get("cover_url"),

                content_tags=tuple(genre.get("name", "") for genre in value.get("genres") or []),

                web_url=f"{self.base_url}/series/{slug}",

            ))

        return {"items": items, "has_more": int(data.get("page", 1)) < int(data.get("lastPage", 1))}



    async def _listing(self, page: int, query: str, genre: str, sort: str):

        params = {"page": str(page), "sort": sort, "x-sveltekit-invalidated": "001"}

        if query:

            params["search"] = query

        if genre:

            params["genre"] = genre

        response = await self._request("GET", f"{self.base_url}/series/__data.json", params=params)

        response.raise_for_status()

        return self._browse_result(response)



    async def browse(self, kind: str, page: int = 1):

        order = {"popular": "popular", "latest": "recent"}.get(kind)

        return await self._listing(page, "", "", order) if order else {"items": [], "has_more": False}



    async def search(self, query: str, page: int = 1, filters: dict | None = None):

        filters = filters or {}

        return await self._listing(page, query.strip(), str(filters.get("genre", "")), str(filters.get("sort", "name")))



    async def _remote_chunks(self, slug: str) -> None:

        if self._details_chunk and self._chapters_chunk:

            return

        response = await self._request("GET", f"{self.base_url}/series/{slug}")

        response.raise_for_status()

        documents = [response.text]

        root = _parse_html(response.text)

        for script in root.descendants("script"):

            src = script.attrs.get("src", "")

            if not src:

                continue

            script_response = await self._request("GET", urljoin(str(response.url), src))

            if getattr(script_response, "status_code", 200) < 400:

                documents.append(script_response.text)

        content = "\n".join(documents)

        details = re.search(r"/_app/remote/([^/]+)/getSerieDetails", content)

        chapters = re.search(r"/_app/remote/([^/]+)/getChapters", content)

        if not details or not chapters:

            raise SourceNotFoundError("CatManhwas no publicó sus endpoints remotos")

        self._details_chunk, self._chapters_chunk = details.group(1), chapters.group(1)



    @staticmethod

    def _chapter_payload(slug: str, page: int) -> str:

        value = [["__skrao", 1], {"page": 2, "slug": 3, "perPage": 4}, page, slug, 100]

        return base64.b64encode(json.dumps(value, separators=(",", ":")).encode()).decode()



    async def _chapter_page(self, slug: str, page: int) -> dict:

        response = await self._request(

            "GET",

            f"{self.base_url}/_app/remote/{self._chapters_chunk}/getChapters",

            params={"payload": self._chapter_payload(slug, page)},

        )

        response.raise_for_status()

        result = self._payload(response).get("result", "[]")

        return self._decode_svelte(json.loads(result))



    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:

        slug = series.source_id if isinstance(series, SourceSeries) else series

        await self._remote_chunks(slug)

        page, result = 1, []

        while True:

            data = await self._chapter_page(slug, page)

            for value in data.get("data", []):

                number = float(value.get("number", 0))

                text = str(number).removesuffix(".0")

                title = f"Capítulo {text}" + (f": {value['name']}" if value.get("name") else "")

                result.append(SourceChapter(

                    source_id=f"{slug}/{value['id']}", title=title, series_id=slug,

                    source_name=self.name, number=number, language=self.language,

                    uploaded_at=value.get("published_at"),

                ))

            pagination = data.get("pagination", {})

            if int(pagination.get("current_page", page)) >= int(pagination.get("last_page", page)):

                return result

            page += 1



    @staticmethod

    def _inside(node, class_name: str) -> bool:

        parent = node.parent

        while parent is not None:

            if parent.has_class(class_name):

                return True

            parent = parent.parent

        return False



    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:

        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter

        response = await self._request("GET", f"{self.base_url}/series/{chapter_id}")

        response.raise_for_status()

        root = _parse_html(response.text)

        urls = [

            _image_url(image, str(response.url)) for image in root.descendants("img")

            if self._inside(image, "w-full") and self._inside(image, "items-center")

        ]

        return [SourcePage(

            source_id=url, chapter_id=chapter_id, index=index,

            filename=url.rsplit("/", 1)[-1].split("?", 1)[0] or f"{index}.jpg",

            source_name=self.name,

        ) for index, url in enumerate(urls)]





SOURCE = CatManhwasSource