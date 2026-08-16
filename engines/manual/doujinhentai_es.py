try:

    from .base import FuenteBaseSource, _Node, _TreeParser

except ImportError:

    pass



class FuenteBaseSource:

    pass





class DoujinhentaiSource(FuenteBaseSource):

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

                                                                "name": "Ahegao",

                                                                "value": "ahegao"

                                                },

                                                {

                                                                "name": "Anal",

                                                                "value": "anal"

                                                },

                                                {

                                                                "name": "Bikini",

                                                                "value": "bikini"

                                                },

                                                {

                                                                "name": "Casadas",

                                                                "value": "casadas"

                                                },

                                                {

                                                                "name": "Chica Con Pene",

                                                                "value": "chica-con-pene"

                                                },

                                                {

                                                                "name": "Cosplay",

                                                                "value": "cosplay"

                                                },

                                                {

                                                                "name": "Doble Penetracion",

                                                                "value": "doble-penetracion"

                                                },

                                                {

                                                                "name": "Ecchi",

                                                                "value": "ecchi"

                                                },

                                                {

                                                                "name": "Embarazada",

                                                                "value": "embarazada"

                                                },

                                                {

                                                                "name": "Enfermera",

                                                                "value": "enfermera"

                                                },

                                                {

                                                                "name": "Escolares",

                                                                "value": "escolares"

                                                },

                                                {

                                                                "name": "Full Color",

                                                                "value": "full-colo"

                                                },

                                                {

                                                                "name": "Futanari",

                                                                "value": "futanari"

                                                },

                                                {

                                                                "name": "Grandes Pechos",

                                                                "value": "grandes-pechos"

                                                },

                                                {

                                                                "name": "Harem",

                                                                "value": "harem"

                                                },

                                                {

                                                                "name": "Incesto",

                                                                "value": "incesto"

                                                },

                                                {

                                                                "name": "Interracial",

                                                                "value": "interracial"

                                                },

                                                {

                                                                "name": "Juguetes Sexuales",

                                                                "value": "juguetes-sexuales"

                                                },

                                                {

                                                                "name": "Lolicon",

                                                                "value": "lolicon"

                                                },

                                                {

                                                                "name": "Maduras",

                                                                "value": "maduras"

                                                },

                                                {

                                                                "name": "Mamadas",

                                                                "value": "mamadas"

                                                },

                                                {

                                                                "name": "Masturbacion",

                                                                "value": "masturbacion"

                                                },

                                                {

                                                                "name": "MILF",

                                                                "value": "milf"

                                                },

                                                {

                                                                "name": "Orgias",

                                                                "value": "orgias"

                                                },

                                                {

                                                                "name": "Profesores",

                                                                "value": "profesores"

                                                },

                                                {

                                                                "name": "Romance",

                                                                "value": "romance"

                                                },

                                                {

                                                                "name": "Shota",

                                                                "value": "shota"

                                                },

                                                {

                                                                "name": "Sin Censura",

                                                                "value": "sin-censura"

                                                },

                                                {

                                                                "name": "Sirvientas",

                                                                "value": "sirvientas"

                                                },

                                                {

                                                                "name": "Tentaculos",

                                                                "value": "tentaculos"

                                                },

                                                {

                                                                "name": "Tetonas",

                                                                "value": "tetonas"

                                                },

                                                {

                                                                "name": "Virgenes",

                                                                "value": "virgenes"

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

                                                                "name": "Doujin",

                                                                "value": "doujin"

                                                },

                                                {

                                                                "name": "Manga",

                                                                "value": "manga"

                                                },

                                                {

                                                                "name": "Comic",

                                                                "value": "comic"

                                                },

                                                {

                                                                "name": "Alfabético",

                                                                "value": "alphabet"

                                                },

                                                {

                                                                "name": "Más vistos",

                                                                "value": "views"

                                                },

                                                {

                                                                "name": "Más recientes",

                                                                "value": "last"

                                                },

                                                {

                                                                "name": "#  (0-9)",

                                                                "value": "0"

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

                                                                "name": "Ñ",

                                                                "value": "ñ"

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

                                                }

                                ],

                                "default": "ahegao"

                }

]

        return [SourceFilter(**item) for item in data]



    name = 'doujinhentai_es'

    display_name = 'DoujinHentai'

    base_url = 'https://doujinhentai.net'

    language = 'es'

    requests_per_minute = 60





class DoujinHentaiSource(GeneratedGenericSource):

    content_warning = "nsfw"



    _genres = [

        ("", "<todos>"), ("ahegao", "Ahegao"), ("anal", "Anal"), ("bikini", "Bikini"),

        ("casadas", "Casadas"), ("chica-con-pene", "Chica Con Pene"), ("cosplay", "Cosplay"),

        ("doble-penetracion", "Doble Penetracion"), ("ecchi", "Ecchi"),

        ("embarazada", "Embarazada"), ("enfermera", "Enfermera"), ("escolares", "Escolares"),

        ("full-colo", "Full Color"), ("futanari", "Futanari"),

        ("grandes-pechos", "Grandes Pechos"), ("harem", "Harem"), ("incesto", "Incesto"),

        ("interracial", "Interracial"), ("juguetes-sexuales", "Juguetes Sexuales"),

        ("lolicon", "Lolicon"), ("maduras", "Maduras"), ("mamadas", "Mamadas"),

        ("masturbacion", "Masturbacion"), ("milf", "MILF"), ("orgias", "Orgias"),

        ("profesores", "Profesores"), ("romance", "Romance"), ("shota", "Shota"),

        ("sin-censura", "Sin Censura"), ("sirvientas", "Sirvientas"),

        ("tentaculos", "Tentaculos"), ("tetonas", "Tetonas"), ("virgenes", "Virgenes"),

        ("yaoi", "Yaoi"), ("yuri", "Yuri"),

    ]



    def get_preferences(self) -> list[SourcePreference]:

        return []



    def get_filters(self) -> list[SourceFilter]:

        letters = [("", "<todas>"), ("0", "# (0-9)")]

        letters.extend((letter.lower(), letter) for letter in "ABCDEFGHIJKLMN")

        letters.append(("\u00f1", "\u00d1"))

        letters.extend((letter.lower(), letter) for letter in "OPQRSTUVWXYZ")

        return [

            SourceFilter("genre", "Genero", "select", self._genres, ""),

            SourceFilter("type", "Tipo de obra", "select", [

                ("", "<todos>"), ("doujin", "Doujin"), ("manga", "Manga"), ("comic", "Comic"),

            ], ""),

            SourceFilter("sort", "Ordenar por (sin otros filtros)", "select", [

                ("alphabet", "Alfabetico"), ("views", "Mas vistos"), ("last", "Mas recientes"),

            ], "alphabet"),

            SourceFilter("artist", "Artista", "text", default=""),

            SourceFilter("author", "Autor", "text", default=""),

            SourceFilter("scanlator", "Scanlator/usuario", "text", default=""),

            SourceFilter("letter", "Primera letra", "select", letters, ""),

        ]



    @staticmethod

    def _all_classes(node, *names: str) -> bool:

        values = node.attrs.get("class", "").split()

        return all(name in values for name in names)



    def _listing(self, response) -> dict:

        root = _parse_html(response.text)

        items = []

        for anchor in root.descendants("a"):

            if not anchor.has_class("block") or not anchor.attrs.get("href"):

                continue

            if not self._inside_classes(anchor, "group", "bg-white", "rounded-2xl"):

                continue

            title_node = _first(anchor, lambda node: node.tag == "h3" and node.has_class("font-bold"))

            if title_node is None:

                continue

            image = _first(anchor, lambda node: node.tag == "img")

            source_id = urljoin(str(response.url), anchor.attrs["href"])

            items.append(SourceSeries(

                source_id=source_id, title=title_node.text().strip(), source_name=self.name,

                cover_url=_image_url(image, str(response.url)) if image else None, web_url=source_id,

            ))

        return {

            "items": items,

            "has_more": any(node.tag == "a" and node.attrs.get("rel") == "next" for node in root.descendants()),

        }



    async def browse(self, kind: str, page: int = 1):

        if kind not in {"popular", "latest"}:

            return {"items": [], "has_more": False}

        response = await self._request("GET", f"{self.base_url}/lista-manga-hentai", params={

            "orderby": "views" if kind == "popular" else "last", "page": str(page),

        })

        response.raise_for_status()

        return self._listing(response)



    async def search(self, query: str, page: int = 1, filters: dict | None = None):

        from urllib.parse import quote

        values = filters or {}

        query = query.strip()

        params = {"page": str(page)}

        if query:

            path = "lista-manga-hentai"

            params["search"] = query

        else:

            genre = str(values.get("genre", "")).strip()

            artist = str(values.get("artist", "")).strip()

            author = str(values.get("author", "")).strip()

            scanlator = str(values.get("scanlator", "")).strip()

            letter = str(values.get("letter", "")).strip()

            work_type = str(values.get("type", "")).strip()

            if genre:

                path = f"lista-manga-hentai/category/{quote(genre, safe='')}"

            elif artist:

                path = f"lista-manga-hentai/artist/{quote(artist, safe='')}"

            elif author:

                path = f"lista-manga-hentai/author/{quote(author, safe='')}"

            elif scanlator:

                path = f"user/{quote(scanlator, safe='')}"

            elif letter:

                path = f"lista-manga-hentai/letra/{quote(letter, safe='')}"

            elif work_type:

                path = f"lista-de-{quote(work_type, safe='')}"

            else:

                path = "lista-manga-hentai"

                if values.get("sort", "alphabet"):

                    params["orderby"] = str(values.get("sort", "alphabet"))

        response = await self._request("GET", f"{self.base_url}/{path}", params=params)

        response.raise_for_status()

        return self._listing(response)



    @staticmethod

    def _inside_classes(node, *names: str) -> bool:

        parent = node.parent

        while parent is not None:

            if DoujinHentaiSource._all_classes(parent, *names):

                return True

            parent = parent.parent

        return False



    @staticmethod

    def _date(value: str) -> str | None:

        from datetime import datetime

        try:

            return datetime.strptime(value.strip(), "%d %b. %Y").isoformat()

        except ValueError:

            return None



    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:

        series_id = series.source_id if isinstance(series, SourceSeries) else series

        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))

        response.raise_for_status()

        root = _parse_html(response.text)

        result = []

        for item in root.descendants("div"):

            if not self._all_classes(item, "flex", "items-center", "gap-4", "p-3", "mb-2", "border", "rounded-lg"):

                continue

            links = [node for node in item.descendants("a") if node.attrs.get("href") and self._inside_classes(node, "flex-1")]

            chapter_link = next((node for node in links if node.has_class("font-bold")), links[0] if links else None)

            if chapter_link is None:

                continue

            base_name = chapter_link.text().strip().removeprefix("Leer ")

            subtitle_node = _first(item, lambda node: node.tag == "div" and self._all_classes(node, "text-sm", "font-medium") and self._inside_classes(node, "flex-1"))

            subtitle = subtitle_node.text().strip() if subtitle_node else ""

            title = f"{base_name}: {subtitle}" if subtitle and subtitle != base_name else base_name

            right = _first(item, lambda node: node.tag == "div" and self._all_classes(node, "text-sm", "text-right"))

            scanlator = ""

            date = None

            if right:

                user = _first(right, lambda node: node.tag == "a" and "/user/" in node.attrs.get("href", ""))

                scanlator = user.text().strip() if user else ""

                dates = [node for node in right.descendants("span") if node.has_class("font-medium")]

                date = self._date(dates[-1].text()) if dates else None

            found = re.search(r"\d+(?:\.\d+)?", base_name)

            result.append(SourceChapter(

                source_id=urljoin(str(response.url), chapter_link.attrs["href"]), title=title,

                series_id=series_id, source_name=self.name,

                number=float(found.group()) if found else None, scanlator=scanlator,

                language=self.language, uploaded_at=date,

            ))

        return result



    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:

        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter

        response = await self._request("GET", urljoin(f"{self.base_url}/", chapter_id))

        response.raise_for_status()

        root = _parse_html(response.text)

        urls = []

        script = next((node.text() for node in root.descendants("script") if "pageUrls" in node.text()), "")

        match = re.search(r"const\s+pageUrls\s*=\s*(\{[^;]+\})", script)

        if match:

            entries = re.findall(r'"(\d+)"\s*:\s*"([^"]+)"', match.group(1))

            urls = [value.replace("\\/", "/") for _, value in sorted(entries, key=lambda entry: int(entry[0]))]

        if not urls:

            container = _first(root, lambda node: node.tag == "div" and node.attrs.get("id") == "vertical-pages-container")

            if container:

                urls = [

                    _image_url(image, str(response.url))

                    for page in container.descendants("div") if page.attrs.get("data-page")

                    for image in [_first(page, lambda node: node.tag == "img")]

                    if image is not None

                ]

        if not urls:

            urls = [

                _image_url(image, str(response.url))

                for image in root.descendants("img")

                if image.has_class("manga-image") and self._inside_classes(image, "single-page-mode")

            ]

        return [SourcePage(

            source_id=urljoin(str(response.url), url), chapter_id=chapter_id, index=index,

            filename=urlparse(url).path.rsplit("/", 1)[-1] or f"{index}.jpg", source_name=self.name,

        ) for index, url in enumerate(urls)]





SOURCE = DoujinHentaiSource