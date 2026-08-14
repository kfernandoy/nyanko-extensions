try:
    from .madara import MadaraSource, _Node, _TreeParser
except ImportError:
    pass

class MadaraSource:
    pass


class ComicskingdomSource(MadaraSource):
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

    name = 'comicskingdom_es'
    display_name = 'Comics Kingdom'
    base_url = 'https://wp.comicskingdom.com'
    language = 'es'
    requests_per_minute = 60


class ComicsKingdomSource(GeneratedGenericSource):
    _manga_fields = "id,link,title,content,meta,yoast_head"
    _chapter_fields = "id,date,assets,link"
    _genres = [
        ("action", "Action"), ("adventure", "Adventure"), ("classic", "Classic"),
        ("comedy", "Comedy"), ("crime", "Crime"), ("fantasy", "Fantasy"),
        ("gag-cartoons", "Gag Cartoons"), ("mystery", "Mystery"),
        ("new-arrivals", "New Arrivals"), ("non-fiction", "Non-Fiction"),
        ("offbeat", "OffBeat"), ("political-cartoons", "Political Cartoons"),
        ("romance", "Romance"), ("sci-fi", "Sci-Fi"),
        ("slice-of-life", "Slice Of Life"), ("superhero", "Superhero"),
        ("vintage", "Vintage"),
    ]

    def get_preferences(self) -> list[SourcePreference]:
        return [SourcePreference(
            "compactPref", "Compactar capitulos", "checkbox", default=True,
        )]

    def get_filters(self) -> list[SourceFilter]:
        return [
            SourceFilter("orderby", "Ordenar por", "select", [
                (value, value) for value in (
                    "author", "date", "id", "include", "modified", "parent",
                    "relevance", "title", "rand",
                )
            ], "author"),
            SourceFilter("genres", "Generos", "tri_state", self._genres, {}),
        ]

    @staticmethod
    def _payload(response):
        return response.json() if hasattr(response, "json") else json.loads(response.text)

    def _manga_params(self) -> dict[str, str]:
        return {
            "per_page": "20", "_fields": self._manga_fields, "ck_language": "spanish",
        }

    def _chapter_params(self) -> dict[str, str]:
        return {"per_page": "100", "_fields": self._chapter_fields}

    @staticmethod
    def _thumbnail(value: str) -> str | None:
        found = re.search(r'thumbnailUrl":"(\S+)","dateP', value)
        return found.group(1).replace("\\/", "/") if found else None

    def _series(self, values) -> list[SourceSeries]:
        result = []
        for item in values:
            title = str(item.get("title", {}).get("rendered", ""))
            link = str(item.get("link", ""))
            if not title or not link or item.get("id") is None:
                continue
            params = {**self._manga_params(), "slug": urlparse(link).path.rstrip("/").rsplit("/", 1)[-1]}
            source_id = f"{self.base_url}/wp-json/wp/v2/ck_feature/{item['id']}?{urlencode(params)}"
            result.append(SourceSeries(
                source_id=source_id, title=title, source_name=self.name,
                cover_url=self._thumbnail(str(item.get("yoast_head", ""))), web_url=link,
            ))
        return result

    async def browse(self, kind: str, page: int = 1):
        if kind not in {"popular", "latest"}:
            return {"items": [], "has_more": False}
        params = {
            **self._manga_params(),
            "orderBy": "relevance" if kind == "popular" else "modified",
            "page": str(page),
        }
        response = await self._request("GET", f"{self.base_url}/wp-json/wp/v2/ck_feature", params=params)
        response.raise_for_status()
        items = self._series(self._payload(response))
        return {"items": items, "has_more": len(items) == 20}

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        values = filters or {}
        params = {
            **self._manga_params(), "search": query, "page": str(page),
            "orderby": str(values.get("orderby", "author")),
        }
        genres = values.get("genres", {})
        if isinstance(genres, dict):
            included = [str(slug) for slug, state in genres.items() if state == "include"]
            excluded = [str(slug) for slug, state in genres.items() if state == "exclude"]
            if included:
                params["ck_genre"] = ",".join(included)
            if excluded:
                params["ck_genre_exclude"] = ",".join(excluded)
        response = await self._request("GET", f"{self.base_url}/wp-json/wp/v2/ck_feature", params=params)
        response.raise_for_status()
        items = self._series(self._payload(response))
        return {"items": items, "has_more": len(items) == 20}

    def _compact(self) -> bool:
        return bool(getattr(self, "preferences", {}).get("compactPref", True))

    def _chapter_url(self, suffix: str = "", **params: str) -> str:
        query = urlencode({**self._chapter_params(), **params})
        return f"{self.base_url}/wp-json/wp/v2/ck_comic{suffix}?{query}"

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        response = await self._request("GET", series_id)
        response.raise_for_status()
        manga = self._payload(response)
        link = str(manga.get("link", ""))
        manga_name = urlparse(link).path.rstrip("/").rsplit("/", 1)[-1]
        if self._compact():
            public = await self._request("GET", link)
            public.raise_for_status()
            counts = [int(value) for value in re.findall(r'"totalItems":(\d+)', public.text) if int(value) > 0]
            if not counts:
                raise ValueError("Comics Kingdom no publico el total de entregas")
            total = counts[0]
            from math import ceil
            result = []
            for index in range(ceil(total / 100)):
                first, last = index * 100 + 1, min(total, (index + 1) * 100)
                result.append(SourceChapter(
                    source_id=self._chapter_url(orderBy="date", order="asc", ck_feature=manga_name, page=str(index + 1)),
                    title=f"{first}-{last}", series_id=series_id, source_name=self.name,
                    number=index * 0.01, language=self.language,
                ))
            return list(reversed(result))

        result = []
        page = 1
        number = 0.0
        while True:
            try:
                chapter_response = await self._request(
                    "GET", f"{self.base_url}/wp-json/wp/v2/ck_comic",
                    params={**self._chapter_params(), "order": "desc", "ck_feature": manga_name, "page": str(page)},
                )
                chapter_response.raise_for_status()
                values = self._payload(chapter_response)
            except Exception:
                if result:
                    return result
                raise
            for item in values:
                number += 0.01
                date = str(item.get("date", ""))
                slug = str(item.get("link", "")).removeprefix(self.base_url)
                result.append(SourceChapter(
                    source_id=self._chapter_url(f"/{item.get('id')}", slug=slug),
                    title=date.split("T", 1)[0], series_id=series_id, source_name=self.name,
                    number=number, language=self.language, uploaded_at=date or None,
                ))
            if len(values) < 100:
                return result
            page += 1

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        response = await self._request("GET", chapter_id)
        response.raise_for_status()
        payload = self._payload(response)
        values = payload if self._compact() else [payload]
        urls = [
            str(item.get("assets", {}).get("single", {}).get("url", ""))
            for item in values if item.get("assets", {}).get("single", {}).get("url")
        ]
        return [SourcePage(
            source_id=url, chapter_id=chapter_id, index=index,
            filename=url.rsplit("/", 1)[-1].split("?", 1)[0] or f"{index}.jpg", source_name=self.name,
        ) for index, url in enumerate(urls)]


SOURCE = ComicskingdomSource
