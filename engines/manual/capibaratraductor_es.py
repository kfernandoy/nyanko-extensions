try:

    from .base import FuenteBaseSource, _Node, _TreeParser

except ImportError:

    pass



class FuenteBaseSource:

    pass





class CapibaratraductorSource(FuenteBaseSource):

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

                                                                "name": "Recientes",

                                                                "value": "latest"

                                                },

                                                {

                                                                "name": "Popularidad",

                                                                "value": "popular"

                                                },

                                                {

                                                                "name": "A-Z",

                                                                "value": "alphabetical"

                                                }

                                ],

                                "default": "latest"

                }

]

        return [SourceFilter(**item) for item in data]



    name = 'capibaratraductor_es'

    display_name = 'CapibaraTraductor'

    base_url = 'https://capibaratraductor.com'

    language = 'es'

    requests_per_minute = 180





class CapibaraTraductorSource(GeneratedGenericSource):

    page_limit = 36



    @staticmethod

    def _payload(response):

        try:

            return response.json()

        except (AttributeError, ValueError):

            return json.loads(response.text)



    async def _scans(self, include_nsfw: bool) -> list[tuple[str, str]]:

        result = []

        page = 1

        while True:

            params = {"page": str(page), "sort": "name", "limit": "100"}

            if include_nsfw:

                params["includeNSFW"] = "true"

            response = await self._request("GET", f"{self.base_url}/api/landing/scans", params=params)

            response.raise_for_status()

            data = self._payload(response).get("data", {})

            result.extend((str(item.get("id", "")), str(item.get("name", ""))) for item in data.get("items", []))

            if int(data.get("page", page)) >= int(data.get("maxPage", page)):

                return result

            page += 1



    async def get_filters(self) -> list[SourceFilter]:

        scans = await self._scans(False) + await self._scans(True)

        unique = {value: name for value, name in scans if value}

        return [

            SourceFilter("scanlator", "Scanlator", "select", [("", "Todos")] + sorted(unique.items(), key=lambda item: item[1])),

            SourceFilter(

                "order", "Ordenar por", "select",

                [("latest", "Recientes"), ("popular", "Popularidad"), ("alphabetical", "A-Z")],

                "latest",

            ),

        ]



    def _series(self, response, page: int) -> dict:

        data = self._payload(response).get("data", {})

        items = []

        for value in data.get("items", []):

            manga = value.get("manga") or {}

            organization = value.get("organization") or {}

            slug, organization_slug = manga.get("slug", ""), organization.get("slug", "")

            if not slug or not organization_slug:

                continue

            source_id = f"{slug}/{organization_slug}"

            items.append(SourceSeries(

                source_id=source_id,

                title=str(value.get("title", "")),

                source_name=self.name,

                cover_url=value.get("imageUrl"),

                artist=organization.get("name"),

                web_url=f"{self.base_url}/{organization_slug}/manga/{slug}",

            ))

        return {"items": items, "has_more": page < int(data.get("maxPage", page))}



    async def _listing(self, page: int, order: str, query: str = "", scanlator: str = ""):

        params = {"page": str(page), "limit": str(self.page_limit), "order": order}

        if query:

            params["search"] = query

        kwargs = {"params": params}

        if scanlator:

            kwargs["headers"] = {"x-organization": scanlator}

        response = await self._request("GET", f"{self.base_url}/api/manga-custom", **kwargs)

        response.raise_for_status()

        return self._series(response, page)



    async def browse(self, kind: str, page: int = 1):

        if kind not in {"popular", "latest"}:

            return {"items": [], "has_more": False}

        return await self._listing(page, kind)



    async def search(self, query: str, page: int = 1, filters: dict | None = None):

        filters = filters or {}

        return await self._listing(

            page,

            str(filters.get("order", "latest")),

            query.strip(),

            str(filters.get("scanlator", "")),

        )



    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:

        series_id = series.source_id if isinstance(series, SourceSeries) else series

        series_slug, organization_slug = series_id.split("/", 1)

        response = await self._request(

            "GET",

            f"{self.base_url}/api/manga-custom/{series_slug}",

            headers={"x-organization": organization_slug},

        )

        response.raise_for_status()

        data = self._payload(response).get("data", {})

        result = []

        for chapter in data.get("chapters") or []:

            if chapter.get("isUnreleased"):

                continue

            number = float(chapter.get("number", 0))

            number_text = str(number).removesuffix(".0")

            result.append(SourceChapter(

                source_id=f"{number_text}/{series_slug}/{organization_slug}",

                title=f"Capítulo {number_text} - {chapter.get('title', '')}",

                series_id=series_id,

                source_name=self.name,

                number=number,

                language=self.language,

                uploaded_at=chapter.get("releasedAt"),

            ))

        return result



    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:

        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter

        chapter_slug, series_slug, organization_slug = chapter_id.split("/", 2)

        response = await self._request(

            "GET",

            f"{self.base_url}/api/manga-custom/{series_slug}/chapter/{chapter_slug}/pages",

            headers={"x-organization": organization_slug},

        )

        response.raise_for_status()

        values = self._payload(response).get("data", [])

        return [SourcePage(

            source_id=str(value["imageUrl"]),

            chapter_id=chapter_id,

            index=index,

            filename=str(value["imageUrl"]).rsplit("/", 1)[-1].split("?", 1)[0] or f"{index}.jpg",

            source_name=self.name,

        ) for index, value in enumerate(values) if value.get("imageUrl")]





SOURCE = CapibaraTraductorSource