try:
    from .base import (
        FuenteBaseSource, _Node, _TreeParser
    )
except ImportError:
    pass

class FuenteBaseSource:
    pass


def _xkcd_children(node: _Node, tag: str | None = None) -> list[_Node]:
    return [
        child
        for child in node.children
        if isinstance(child, _Node) and (tag is None or child.tag == tag)
    ]


def _xkcd_is_last_element(node: _Node) -> bool:
    parent = node.parent
    if parent is None:
        return True
    elements = _xkcd_children(parent)
    return bool(elements) and elements[-1] is node


def _xkcd_by_id(root: _Node, identifier: str) -> _Node | None:
    return _first(root, lambda node: node.attrs.get("id") == identifier)


class XkcdSource(FuenteBaseSource):
    """El numero de tira es comun a todos los idiomas; las fechas salen del archivo ingles."""

    supports_latest = False

    def __init__(self, fetcher: SourceFetcher | None = None) -> None:
        super().__init__(fetcher)
        self._dates: dict[int, str] | None = None
        self._dates_at = 0.0
        self._chapters: list[SourceChapter] | None = None
        self._chapters_at = 0.0

    # ---------------------------------------------------------------- config
    @property
    def archive_path(self) -> str:
        return _XKCD_ARCHIVE.get(self.language, "/archive")

    @property
    def creator(self) -> str:
        return _XKCD_CREATOR.get(self.language, "Randall Munroe")

    @property
    def synopsis(self) -> str:
        return _XKCD_SYNOPSIS.get(self.language, "A webcomic of romance, sarcasm, math and language.")

    @property
    def interactive_text(self) -> str:
        return _XKCD_INTERACTIVE.get(
            self.language, "To experience the interactive version of this comic, open it in WebView/browser.",
        )

    def get_filters(self) -> list[SourceFilter]:
        return []

    def get_preferences(self) -> list[SourcePreference]:
        return [
            SourcePreference(
                id="organization_method",
                name="Organization Method",
                type="select",
                options=[
                    ("SINGLE", "Single manga (all comics)"),
                    ("BY_YEAR", "By year"),
                    ("BY_YEAR_MONTH", "By year-month"),
                ],
                default="SINGLE",
            )
        ]

    # --------------------------------------------------------------- catalog
    async def browse(self, kind: str, page: int = 1):
        if kind != "popular":
            return {"items": [], "has_more": False}
        groups = await self._grouped()
        keys = sorted(groups, reverse=True)
        start = (page - 1) * _XKCD_PER_PAGE
        window = keys[start : start + _XKCD_PER_PAGE]
        items: list[SourceSeries] = []
        for key in window:
            first = groups[key][0] if groups[key] else None
            items.append(
                SourceSeries(
                    source_id=key,
                    title="xkcd" if key == "SINGLE" else f"xkcd {key}",
                    source_name=self.name,
                    cover_url=await self._thumbnail(first) if first is not None else None,
                    description=self.synopsis,
                    author=self.creator,
                    artist=self.creator,
                    status="ongoing",
                    web_url=self.base_url,
                )
            )
        return {"items": items, "has_more": start + _XKCD_PER_PAGE < len(keys)}

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        # El Kotlin no implementa busqueda: siempre devuelve vacio.
        return {"items": [], "has_more": False}

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        if isinstance(series, SourceSeries):
            return series
        return SourceSeries(
            source_id=str(series),
            title="xkcd" if str(series) == "SINGLE" else f"xkcd {series}",
            source_name=self.name,
            description=self.synopsis,
            author=self.creator,
            artist=self.creator,
            status="ongoing",
            web_url=self.base_url,
        )

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        key = series.source_id if isinstance(series, SourceSeries) else str(series)
        return (await self._grouped()).get(key, [])

    # ----------------------------------------------------------------- pages
    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else str(chapter)
        response = await self._request("GET", urljoin(f"{self.base_url}/", chapter_id.lstrip("/")))
        response.raise_for_status()
        root = _parse_html(response.text)
        base = str(response.url) or self.base_url
        container = self._container(root)
        if container is None:
            raise ValueError(self.interactive_text)
        image = self._image_node(container)
        if image is None:
            raise ValueError(self.interactive_text)
        source = self._image_url(image, base)
        first, second = self._texts(root, image)
        return [
            SourcePage(
                source_id=source,
                chapter_id=chapter_id,
                index=0,
                filename=urlparse(source).path.rsplit("/", 1)[-1] or "0.png",
                source_name=self.name,
            ),
            SourcePage(
                source_id=_XKCD_TEXT + urlencode({"alt": first, "title": second}),
                chapter_id=chapter_id,
                index=1,
                filename="1.png",
                source_name=self.name,
            ),
        ]

    async def page_bytes(self, page: SourcePage | str) -> SourcePageContent:
        url = page.source_id if isinstance(page, SourcePage) else str(page)
        if not url.startswith(_XKCD_TEXT):
            return await super().page_bytes(page)
        # El Kotlin delega en TextInterceptor; aqui la tira de texto se dibuja.
        values = parse_qs(url[len(_XKCD_TEXT):])
        rendered = self._render(
            values.get("alt", [""])[0], values.get("title", [""])[0],
        )
        return SourcePageContent(media_type="image/png", chunks=iter([rendered]))

    # -------------------------------------------------------------- internals
    async def _grouped(self) -> dict[str, list[SourceChapter]]:
        # Solo el modo SINGLE es alcanzable: la app no devuelve el valor elegido.
        return {"SINGLE": await self._all_chapters()}

    async def _all_chapters(self) -> list[SourceChapter]:
        import time

        now = time.time()
        if self._chapters is None or now - self._chapters_at > _XKCD_CACHE_SECONDS:
            response = await self._request("GET", f"{self.base_url}{self.archive_path}")
            response.raise_for_status()
            self._chapters = await self._parse_archive(response)
            self._chapters_at = now
        return self._chapters

    async def _english_dates(self) -> dict[int, str]:
        import time

        now = time.time()
        if self._dates is None or now - self._dates_at > _XKCD_CACHE_SECONDS:
            try:
                response = await self._request("GET", f"{_XKCD_ENGLISH}/archive/")
                response.raise_for_status()
                root = _parse_html(response.text)
                holder = _xkcd_by_id(root, "middleContainer")
                self._dates = {
                    number: anchor.attrs.get("title", "")
                    for anchor in (_xkcd_children(holder, "a") if holder is not None else [])
                    if (number := self._number(anchor.attrs.get("href", ""))) is not None
                }
            except Exception:
                self._dates = {}
            self._dates_at = now
        return self._dates

    async def _parse_archive(self, response: Any) -> list[SourceChapter]:
        dates = await self._english_dates()
        if self.language == "zh":
            payload = response.json() or {}
            return [
                self._chapter(
                    f"/{item['id']}", int(item["id"]), str(item.get("title") or ""),
                    dates.get(int(item["id"])),
                )
                for item in payload.values()
                if isinstance(item, dict) and item.get("id") is not None
            ]
        root = _parse_html(response.text)
        base = str(response.url) or self.base_url
        anchors = self._archive_anchors(root)
        result: list[SourceChapter] = []
        if self.language == "es":
            by_date = {self._normalize(value): number for number, value in dates.items()}
            for anchor in anchors:
                parent = anchor.parent
                moment = _first(parent, lambda node: node.tag == "time") if parent is not None else None
                if moment is None:
                    continue
                stamp = moment.text().strip()
                path = urlparse(urljoin(base, anchor.attrs.get("href", ""))).path
                number = _XKCD_SPANISH_OVERRIDES.get(path) or by_date.get(self._normalize(stamp))
                if number is None:
                    continue
                result.append(self._chapter(path, number, anchor.text().strip(), stamp))
            return result
        for anchor in anchors:
            parsed = urlparse(urljoin(base, anchor.attrs.get("href", "")))
            path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
            if self.language == "fr":
                # La tira va en la query: /tous-episodes.php?num=123
                number = self._int(path.rpartition("=")[2])
                title = anchor.text().strip()
            elif self.language == "ru":
                number = self._number(parsed.path)
                children = _xkcd_children(anchor)
                title = children[0].attrs.get("alt", "") if children else ""
            else:
                number = self._number(parsed.path)
                title = anchor.text().strip()
            result.append(
                self._chapter(
                    path, number or 0, title,
                    dates.get(number) if number is not None else None,
                )
            )
        return result[::-1] if self.language == "fr" else result

    def _archive_anchors(self, root: _Node) -> list[_Node]:
        if self.language == "es":
            return [
                anchor
                for holder in root.descendants()
                if holder.has_class("archive-entry")
                for anchor in _xkcd_children(holder, "a")
            ]
        if self.language == "fr":
            content = _xkcd_by_id(root, "content")
            anchors: list[_Node] = []
            for holder in content.descendants() if content is not None else []:
                if not holder.has_class("s"):
                    continue
                found = _xkcd_children(holder, "a")
                # ":not(:last-of-type)" descarta el ultimo enlace del bloque.
                anchors.extend(found[:-1] if found else [])
            return anchors
        if self.language == "ru":
            return [
                anchor
                for holder in root.descendants()
                if holder.has_class("main")
                for anchor in _xkcd_children(holder, "a")
            ]
        holder = _xkcd_by_id(root, "middleContainer")
        return _xkcd_children(holder, "a") if holder is not None else []

    def _container(self, root: _Node) -> _Node | None:
        if self.language == "es":
            content = _xkcd_by_id(root, "middleContent")
            return next(
                (node for node in content.descendants() if node.has_class("strip")),
                None,
            ) if content is not None else None
        if self.language == "fr":
            content = _xkcd_by_id(root, "content")
            return next(
                (node for node in content.descendants() if node.has_class("s")), None,
            ) if content is not None else None
        if self.language == "ru":
            return next((node for node in root.descendants() if node.has_class("main")), None)
        if self.language == "zh":
            content = _xkcd_by_id(root, "content")
            return next(
                (node for node in _xkcd_children(content, "img") if not node.attrs.get("id")), None,
            ) if content is not None else None
        comic = _xkcd_by_id(root, "comic")
        return next(iter(_xkcd_children(comic, "img")), None) if comic is not None else None

    def _image_node(self, container: _Node) -> _Node | None:
        if self.language == "fr":
            return _first(
                container,
                lambda node: node.tag == "img" and node.attrs.get("src", "").startswith("strips/"),
            )
        if self.language == "ru":
            return _first(
                container, lambda node: node.tag == "img" and "/i/" in node.attrs.get("src", ""),
            )
        if self.language == "zh":
            return container
        return container if _xkcd_is_last_element(container) else None

    def _image_url(self, image: _Node, base: str) -> str:
        if self.language in {"fr", "ru", "zh"} or not image.attrs.get("srcset"):
            return urljoin(base, image.attrs.get("src", ""))
        return urljoin(base, image.attrs["srcset"].split(" ", 1)[0])

    def _texts(self, root: _Node, image: _Node) -> tuple[str, str]:
        first = image.attrs.get("alt", "")
        second = image.attrs.get("title", "")
        if self.language == "fr":
            content = _xkcd_by_id(root, "content")
            block = next(
                (
                    node
                    for holder in (content.descendants() if content is not None else [])
                    if holder.has_class("s")
                    for node in holder.descendants("div")
                    if not node.has_class("buttons")
                ),
                None,
            )
            first = (block.text().strip() if block is not None else "") or first
        if self.language == "ru":
            block = next(
                (node for node in root.descendants() if node.has_class("comics_text")), None,
            )
            second = (block.text().strip() if block is not None else "") or first
        return first, second

    async def _thumbnail(self, chapter: SourceChapter) -> str | None:
        try:
            response = await self._request(
                "GET", urljoin(f"{self.base_url}/", chapter.source_id.lstrip("/")),
            )
            response.raise_for_status()
            root = _parse_html(response.text)
            base = str(response.url) or self.base_url
            container = self._container(root)
            image = None
            if container is not None:
                image = (
                    self._image_node(container)
                    if self.language in {"fr", "ru"}
                    else container
                )
            image = image or _first(root, lambda node: node.tag == "img" and node.attrs.get("alt"))
            if image is None:
                return None
            value = self._image_url(image, base)
            return value if value and "thumbnail" not in value else None
        except Exception:
            return None

    def _chapter(self, path: str, number: int, title: str, stamp: str | None) -> SourceChapter:
        return SourceChapter(
            source_id=path.lstrip("/"),
            title=f"{number}: {title}",
            series_id="SINGLE",
            source_name=self.name,
            number=float(number),
            language=self.language,
            uploaded_at=self._date(stamp),
        )

    @staticmethod
    def _render(alt: str, title: str) -> bytes:
        import textwrap

        from PIL import ImageDraw, ImageFont

        font = ImageFont.load_default()
        lines: list[str] = []
        for block in (alt, title):
            if not block:
                continue
            if lines:
                lines.append("")
            lines.extend(textwrap.wrap(block, width=60) or [""])
        lines = lines or [""]
        width, height = 640, 24 + 18 * len(lines)
        canvas = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(canvas)
        for index, line in enumerate(lines):
            draw.text((12, 12 + 18 * index), line, fill="black", font=font)
        buffer = io.BytesIO()
        canvas.save(buffer, "PNG")
        return buffer.getvalue()

    @staticmethod
    def _normalize(value: str) -> str:
        parts = value.strip().split("-")
        if len(parts) != 3:
            return value.strip()
        return f"{parts[0]}-{parts[1].zfill(2)}-{parts[2].zfill(2)}"

    @classmethod
    def _number(cls, value: str) -> int | None:
        return cls._int(value.strip("/"))

    @staticmethod
    def _int(value: str) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _date(cls, value: str | None) -> str | None:
        from datetime import datetime

        if not value:
            return None
        try:
            return datetime.strptime(cls._normalize(value), "%Y-%m-%d").isoformat()
        except ValueError:
            return None




SOURCE = XkcdSource
