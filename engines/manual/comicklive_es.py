try:

    from .base import FuenteBaseSource, _Node, _TreeParser

except ImportError:

    pass



class FuenteBaseSource:

    pass





class ComickliveSource(FuenteBaseSource):

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

        data = []

        return [SourceFilter(**item) for item in data]



    name = 'comicklive_es'

    display_name = 'Comick (Unoriginal)'

    base_url = 'https://comick.live'

    language = 'es'

    requests_per_minute = 60





class ComickSource(GeneratedGenericSource):

    requests_per_minute = 30

    _next_cursor = None



    def get_preferences(self) -> list[SourcePreference]:

        return [SourcePreference(

            "get_tags", "Mostrar etiquetas como lista", "checkbox", default=True,

        )]



    @staticmethod

    def _fixed_filters() -> list[SourceFilter]:

        from datetime import datetime

        years = [(str(year), str(year)) for year in range(datetime.now().year, 1989, -1)]

        years.append(("0", "Antes de 1990"))

        return [

            SourceFilter("order_by", "Ordenar por", "sort", [

                ("created_at", "Mas recientes"), ("user_follow_count", "Popular"),

                ("rating", "Mejor valorados"), ("uploaded", "Ultima subida"),

            ], "created_at"),

            SourceFilter("order_direction", "Direccion", "select", [("desc", "Descendente"), ("asc", "Ascendente")], "desc"),

            SourceFilter("demographic", "Demografia", "multi_select", [

                ("1", "Shounen"), ("2", "Josei"), ("3", "Seinen"), ("4", "Shoujo"), ("0", "Ninguna"),

            ], []),

            SourceFilter("country", "Tipo", "multi_select", [

                ("jp", "Manga"), ("kr", "Manhwa"), ("cn", "Manhua"), ("others", "Otros"),

            ], []),

            SourceFilter("time", "Creado", "select", [

                ("", "Cualquier fecha"), ("3", "Hace 3 dias"), ("7", "Hace 7 dias"),

                ("30", "Hace 30 dias"), ("90", "Hace 3 meses"), ("180", "Hace 6 meses"),

                ("365", "Hace 1 ano"), ("730", "Hace 2 anos"),

            ], ""),

            SourceFilter("minimum", "Capitulos minimos", "text", default=""),

            SourceFilter("status", "Estado", "select", [

                ("", "Todos"), ("1", "En curso"), ("2", "Completado"),

                ("3", "Cancelado"), ("4", "En pausa"),

            ], ""),

            SourceFilter("content_rating", "Clasificacion", "select", [

                ("", "Todas"), ("safe", "Seguro"), ("suggestive", "Sugestivo"), ("erotica", "Erotica"),

            ], ""),

            SourceFilter("from", "Publicado desde", "select", [("", "Cualquier ano"), *years], ""),

            SourceFilter("to", "Publicado hasta", "select", [("", "Cualquier ano"), *years], ""),

        ]



    async def get_filters(self) -> list[SourceFilter]:

        filters = self._fixed_filters()

        get_tags = bool(getattr(self, "preferences", {}).get("get_tags", True))

        try:

            response = await self._request("GET", f"{self.base_url}/api/metadata")

            response.raise_for_status()

            payload = response.json() if hasattr(response, "json") else json.loads(response.text)

            genres = [(str(item["slug"]), str(item["name"])) for item in payload.get("genres", [])]

            filters.insert(4, SourceFilter("genres", "Generos", "tri_state", genres, {}))

            if get_tags:

                tags = [(str(item["slug"]), str(item["name"])) for item in payload.get("tags", [])]

                filters.insert(5, SourceFilter("tags", "Etiquetas", "tri_state", tags, {}))

            else:

                filters.insert(5, SourceFilter("tags_text", "Etiquetas (separadas por coma; - para excluir)", "text", default=""))

        except Exception:

            if not get_tags:

                filters.insert(4, SourceFilter("tags_text", "Etiquetas (separadas por coma; - para excluir)", "text", default=""))

        return filters



    @staticmethod

    def _payload(response):

        return response.json() if hasattr(response, "json") else json.loads(response.text)



    def _series(self, values) -> list[SourceSeries]:

        return [SourceSeries(

            source_id=str(item.get("slug", "")), title=str(item.get("title", "")), source_name=self.name,

            cover_url=item.get("default_thumbnail"),

            web_url=f"{self.base_url}/comic/{item.get('slug', '')}",

        ) for item in values if item.get("slug") and item.get("title")]



    async def browse(self, kind: str, page: int = 1):

        if kind == "popular":

            if page not in range(1, 7):

                return {"items": [], "has_more": False}

            response = await self._request("GET", f"{self.base_url}/api/comics/top", params={

                "days": str((7, 30, 90)[(page - 1) % 3]),

                "type": "follow" if page <= 3 else "most_follow_new",

            })

            has_more = page < 6

        elif kind == "latest":

            response = await self._request("GET", f"{self.base_url}/api/chapters/latest", params={"order": "new", "page": str(page)})

            has_more = None

        else:

            return {"items": [], "has_more": False}

        response.raise_for_status()

        items = self._series(self._payload(response).get("data", []))

        return {"items": items, "has_more": len(items) == 100 if has_more is None else has_more}



    @staticmethod

    def _tri_state(params: list[tuple[str, str]], values, included: str, excluded: str) -> None:

        if not isinstance(values, dict):

            return

        for slug, state in values.items():

            if state == "include":

                params.append((included, str(slug)))

            elif state == "exclude":

                params.append((excluded, str(slug)))



    async def search(self, query: str, page: int = 1, filters: dict | None = None):

        if page == 1:

            self._next_cursor = None

        query = query.strip()

        if query and len(query) < 3:

            raise ValueError("La busqueda debe tener al menos 3 caracteres")

        values = filters or {}

        minimum = str(values.get("minimum", "")).strip()

        if minimum:

            try:

                int(minimum)

            except ValueError as exc:

                raise ValueError(f"Cantidad minima de capitulos invalida: {minimum}") from exc

        params = [

            ("order_by", str(values.get("order_by", "created_at"))),

            ("order_direction", str(values.get("order_direction", "desc"))),

        ]

        self._tri_state(params, values.get("genres"), "genres", "excludes")

        tag_values = values.get("tags")

        self._tri_state(params, tag_values, "tags", "excluded_tags")

        if not isinstance(tag_values, dict):

            for tag in str(values.get("tags_text", "")).split(","):

                if not tag.strip():

                    continue

                tag = re.sub(r"[ /]", "-", tag.strip().lower())

                params.append(("excluded_tags" if tag.startswith("-") else "tags", tag.replace("-", "", 1) if tag.startswith("-") else tag))

        for key in ("demographic", "country"):

            selected = values.get(key, [])

            if isinstance(selected, list):

                params.extend((key, str(item)) for item in selected)

        for key in ("time", "minimum", "status", "from", "to", "content_rating"):

            if str(values.get(key, "")).strip():

                params.append((key, str(values[key])))

        params.extend((("showAll", "false"), ("exclude_mylist", "false")))

        if query:

            params.append(("q", query))

        params.append(("type", "comic"))

        if page > 1 and self._next_cursor:

            params.append(("cursor", self._next_cursor))

        response = await self._request("GET", f"{self.base_url}/api/search", params=params)

        response.raise_for_status()

        payload = self._payload(response)

        self._next_cursor = payload.get("next_cursor")

        return {"items": self._series(payload.get("data", [])), "has_more": self._next_cursor is not None}



    @staticmethod

    def _slug(series_id: str) -> str:

        parts = [part for part in urlparse(series_id).path.split("/") if part]

        return parts[parts.index("comic") + 1] if "comic" in parts and parts.index("comic") + 1 < len(parts) else series_id.strip("/")



    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:

        series_id = series.source_id if isinstance(series, SourceSeries) else series

        slug = self._slug(series_id)

        page = 1

        chapters = []

        while True:

            params = {"lang": self.language}

            if page > 1:

                params["page"] = str(page)

            response = await self._request("GET", f"{self.base_url}/api/comics/{slug}/chapter-list", params=params)

            response.raise_for_status()

            payload = self._payload(response)

            chapters.extend(payload.get("data", []))

            pagination = payload.get("pagination", {})

            if int(pagination.get("current_page", page)) >= int(pagination.get("last_page", page)):

                break

            page += 1

        result = []

        for item in chapters:

            chapter_number = str(item.get("chap", ""))

            volume = str(item.get("vol") or "").strip()

            title = f"{'Vol. ' + volume + ' ' if volume else ''}Ch. {chapter_number}"

            if str(item.get("title") or "").strip():

                title += f": {str(item['title']).strip()}"

            try:

                number = float(chapter_number)

            except ValueError:

                number = None

            lang = str(item.get("lang", self.language))

            source_id = f"{self.base_url}/comic/{slug}/{item.get('hid', '')}-chapter-{chapter_number}-{lang}"

            result.append(SourceChapter(

                source_id=source_id, title=title, series_id=series_id, source_name=self.name,

                number=number, scanlator=", ".join(item.get("group_name", [])), language=lang,

                uploaded_at=str(item.get("created_at")) if item.get("created_at") else None,

            ))

        return result



    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:

        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter

        response = await self._request("GET", urljoin(f"{self.base_url}/", chapter_id))

        response.raise_for_status()

        root = _parse_html(response.text)

        data_node = _first(root, lambda node: node.attrs.get("id") == "sv-data")

        if data_node is None:

            return []

        payload = json.loads(data_node.text())

        urls = [str(item.get("url", "")) for item in payload.get("chapter", {}).get("images", []) if item.get("url")]

        return [SourcePage(

            source_id=url, chapter_id=chapter_id, index=index,

            filename=url.rsplit("/", 1)[-1].split("?", 1)[0] or f"{index}.jpg", source_name=self.name,

        ) for index, url in enumerate(urls)]





SOURCE = ComickSource