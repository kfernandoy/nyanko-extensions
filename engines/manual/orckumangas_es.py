try:
    from .base import FuenteBaseSource, _Node, _TreeParser
except ImportError:
    pass

class FuenteBaseSource:
    pass


def _orcku_own_text(node: _Node) -> str:
    return " ".join(part.strip() for part in node.children if isinstance(part, str) and part.strip())


def _orcku_kids(node: _Node, tag: str, class_name: str | None = None) -> list[_Node]:
    return [
        child
        for child in node.children
        if isinstance(child, _Node)
        and child.tag == tag
        and (class_name is None or child.has_class(class_name))
    ]


class OrckumangasSource(FuenteBaseSource):
    """Los capitulos llegan paginados y se recorren hasta agotar el enlace siguiente."""

    max_chapter_pages = 50

    def get_filters(self) -> list[SourceFilter]:
        return [
            SourceFilter("genre", "Género", "select", list(_ORCKU_GENRES), "0"),
            SourceFilter("type", "Tipo", "select", [
                ("", "Todos"), ("manga", "Manga"), ("manhwa", "Manhwa"), ("manhua", "Manhua"),
            ], ""),
            SourceFilter("status", "Estado", "select", [
                ("", "Todos"), ("ongoing", "En curso"), ("completed", "Finalizado"),
                ("hiatus", "Hiatus"), ("cancelled", "Cancelado"),
            ], ""),
        ]

    async def browse(self, kind: str, page: int = 1):
        if kind == "popular":
            response = await self._request(
                "GET", f"{self.base_url}/ranking.php", params={"page": str(page)},
            )
            response.raise_for_status()
            return self._cards(response)
        if kind == "latest":
            # El listado de novedades no pagina: siempre devuelve la portada.
            response = await self._request(
                "GET", f"{self.base_url}/index.php", params={"filter_chapters": "1", "type": ""},
            )
            response.raise_for_status()
            root = _parse_html(response.text)
            base = str(response.url) or self.base_url
            blocks = [
                node
                for parent in root.descendants("div")
                for node in _orcku_kids(parent, "a", "block")
            ]
            return {"items": self._entries(blocks, base), "has_more": False}
        return {"items": [], "has_more": False}

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        query = query.strip()
        if query:
            # Una busqueda por texto ignora los filtros, igual que en el Kotlin.
            response = await self._request(
                "GET", f"{self.base_url}/buscador.php", params={"q": query, "page": str(page)},
            )
        else:
            values = filters or {}
            params = [("page", str(page))]
            params.extend(
                (key, str(values.get(key) or default))
                for key, default in (("genre", "0"), ("type", ""), ("status", ""))
            )
            response = await self._request("GET", f"{self.base_url}/biblioteca.php", params=params)
        response.raise_for_status()
        return self._cards(response)

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        base = str(response.url) or self.base_url
        card = next(
            (
                node
                for main in root.descendants("main")
                for node in main.descendants("div")
                if node.has_class("card")
            ),
            None,
        )
        if card is None:
            raise SourceNotFoundError(f"{self.display_name}: ficha sin tarjeta")
        title = _first(card, lambda node: node.tag == "h1")
        image = _first(card, lambda node: node.tag == "img")
        summary = _first(card, lambda node: node.tag == "p")
        return SourceSeries(
            source_id=series_id,
            title=title.text().strip() if title is not None else "",
            source_name=self.name,
            cover_url=_image_url(image, base) or None if image is not None else None,
            description=summary.text().strip() if summary is not None else None,
            author=self._labelled(card, "autor"),
            artist=self._labelled(card, "artista"),
            status=self._status(self._labelled(card, "estado")),
            content_tags=tuple(
                text
                for node in card.descendants("a")
                if "genre" in node.attrs.get("href", "") and (text := node.text().strip())
            ),
            web_url=urljoin(f"{self.base_url}/", series_id),
        )

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        result: list[SourceChapter] = []
        page = 1
        while page <= self.max_chapter_pages:
            response = await self._request(
                "GET",
                urljoin(f"{self.base_url}/", series_id),
                params={"order": "desc", "page": str(page)},
            )
            response.raise_for_status()
            root = _parse_html(response.text)
            base = str(response.url) or self.base_url
            for card in root.descendants("div"):
                if not card.has_class("card"):
                    continue
                for grid in card.descendants("div"):
                    if not grid.has_class("grid"):
                        continue
                    for anchor in _orcku_kids(grid, "a", "block"):
                        label = _first(anchor, lambda node: node.tag == "span")
                        title = _orcku_own_text(label) if label is not None else ""
                        found = _ORCKU_NUMBER.search(title)
                        result.append(
                            SourceChapter(
                                source_id=self._path(anchor.attrs.get("href", ""), base),
                                title=title,
                                series_id=series_id,
                                source_name=self.name,
                                number=float(found.group()) if found else None,
                                language=self.language,
                            )
                        )
            if not self._links_to_page(root, page + 1):
                break
            page += 1
        return list({chapter.source_id: chapter for chapter in result}.values())

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else str(chapter)
        response = await self._request("GET", urljoin(f"{self.base_url}/", chapter_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        base = str(response.url) or self.base_url
        urls = [
            _image_url(node, base)
            for holder in root.descendants("div")
            if holder.has_class("chapter-images")
            for node in holder.descendants("img")
        ]
        return [
            SourcePage(
                source_id=value,
                chapter_id=chapter_id,
                index=index,
                filename=urlparse(value).path.rsplit("/", 1)[-1] or f"{index}.jpg",
                source_name=self.name,
            )
            for index, value in enumerate(value for value in urls if value)
        ]

    def _cards(self, response: Any) -> dict:
        root = _parse_html(response.text)
        base = str(response.url) or self.base_url
        anchors = [
            node
            for holder in root.descendants("div")
            if holder.has_class("card")
            for node in _orcku_kids(holder, "a")
        ]
        return {"items": self._entries(anchors, base), "has_more": self._has_next(root)}

    def _entries(self, anchors: list[_Node], base: str) -> list[SourceSeries]:
        result: dict[str, SourceSeries] = {}
        for anchor in anchors:
            slug = self._path(anchor.attrs.get("href", ""), base)
            heading = _first(anchor, lambda node: node.tag == "h3")
            if not slug or slug in result or heading is None:
                continue
            image = _first(anchor, lambda node: node.tag == "img")
            result[slug] = SourceSeries(
                source_id=slug,
                title=_orcku_own_text(heading),
                source_name=self.name,
                cover_url=_image_url(image, base) or None if image is not None else None,
                web_url=urljoin(f"{self.base_url}/", slug),
            )
        return list(result.values())

    @staticmethod
    def _labelled(card: _Node, label: str) -> str | None:
        for holder in card.descendants("div"):
            for node in _orcku_kids(holder, "span"):
                if label in _orcku_own_text(node).casefold():
                    return _orcku_own_text(holder) or None
        return None

    @staticmethod
    def _status(value: str | None) -> str | None:
        return {
            "ongoing": "ongoing", "completed": "completed",
            "hiatus": "hiatus", "cancelled": "cancelled",
        }.get((value or "").strip().casefold())

    @staticmethod
    def _has_next(root: _Node) -> bool:
        return any(
            "siguiente" in _orcku_own_text(anchor).casefold()
            for holder in root.descendants("div")
            if holder.has_class("flex")
            for anchor in _orcku_kids(holder, "a")
        )

    @staticmethod
    def _links_to_page(root: _Node, page: int) -> bool:
        marker = f"page={page}"
        return any(
            marker in anchor.attrs.get("href", "")
            for holder in root.descendants("div")
            for anchor in _orcku_kids(holder, "a")
        )

    @staticmethod
    def _path(href: str, base: str) -> str:
        parsed = urlparse(urljoin(base, href))
        return f"{parsed.path.lstrip('/')}{'?' + parsed.query if parsed.query else ''}"


class GeneratedOrckuMangasSource(OrckuMangasSource):
    name = 'orckumangas_es'
    display_name = 'Orcku Mangas'
    base_url = 'https://orckumangas.com'
    language = 'es'
    requests_per_minute = 180
    content_warning = 'nsfw'
    image_headers = {'Referer': 'https://orckumangas.com/'}


SOURCE = OrckumangasSource
