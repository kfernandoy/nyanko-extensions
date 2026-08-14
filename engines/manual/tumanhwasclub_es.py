try:
    from .base import FuenteBaseSource, _Node, _TreeParser
except ImportError:
    pass

class FuenteBaseSource:
    pass


def _manhwasme_image(node: _Node, base: str) -> str:
    return urljoin(base, node.attrs.get("data-src") or node.attrs.get("src") or "")


class TumanhwasclubSource(FuenteBaseSource):
    """Las rutas viejas de TuManhwas usaban /manhwa/; el sitio ahora sirve /manga/."""

    def get_filters(self) -> list[SourceFilter]:
        return [
            SourceFilter("sort", "Ordenar Por", "select", [
                ("", "Cualquiera"), ("-updated_at", "Última Actualización"), ("-views", "Más Popular"),
                ("name", "A-Z"), ("-name", "Z-A"), ("-created_at", "Más Recientes"),
            ], ""),
            SourceFilter("type", "Tipos", "select", [("", "Cualquiera")] + [
                (value, label) for value, label in (
                    ("manga", "Manga"), ("manhwa", "Manhwa"), ("manhua", "Manhua"),
                    ("webtoon", "Webtoon"), ("one-shot", "One-shot"), ("doujinshi", "Doujinshi"),
                )
            ], ""),
            SourceFilter("genre", "Géneros", "select", [("", "Cualquiera")] + list(_MANHWASME_GENRES), ""),
            SourceFilter("status", "Estado", "select", [
                ("", "Cualquiera"), ("1", "En Curso"), ("2", "Completado"),
                ("3", "En Pausa"), ("4", "Cancelado"),
            ], ""),
            SourceFilter("caution", "Contenido (+18)", "select", [
                ("", "Cualquiera"), ("0", "No 18+"), ("1", "18+ Only"),
            ], ""),
        ]

    async def browse(self, kind: str, page: int = 1):
        if kind not in {"popular", "latest"}:
            return {"items": [], "has_more": False}
        return await self._search_page([
            ("sort", "-views" if kind == "popular" else "-updated_at"), ("page", str(page)),
        ])

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        values = filters or {}
        params: list[tuple[str, str]] = [("page", str(page))]
        if query.strip():
            params.append(("filter[name]", query.strip()))
        params.extend(
            (key, str(values.get(key) or ""))
            for key in ("sort", "type", "genre", "status", "caution")
        )
        return await self._search_page(params)

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request("GET", self._url(series_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        base = str(response.url) or self.base_url
        title = _first(root, lambda node: node.tag == "h1" and node.has_class("detail-title"))
        cover = next(
            (
                node
                for holder in root.descendants("div")
                if holder.has_class("detail-hero-cover")
                for node in holder.descendants("img")
            ),
            None,
        )
        summary = next(
            (
                node.text().strip()
                for holder in root.descendants("div")
                if holder.has_class("detail-synopsis")
                for node in holder.descendants("p")
            ),
            None,
        )
        year = _first(root, lambda node: node.tag == "span" and node.has_class("detail-tag-year"))
        return SourceSeries(
            source_id=series_id,
            title=title.text().strip() if title is not None else "",
            source_name=self.name,
            cover_url=_manhwasme_image(cover, base) if cover is not None else None,
            description=summary or None,
            author=self._stat(root, "autores"),
            status=self._status(year.text() if year is not None else ""),
            content_tags=tuple(self._stat_links(root, "géneros")),
            web_url=self._url(series_id),
        )

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request("GET", self._url(series_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        base = str(response.url) or self.base_url
        result: list[SourceChapter] = []
        for row in root.descendants("div"):
            if not row.has_class("detail-chapter-row"):
                continue
            anchor = next(
                (
                    node
                    for holder in row.descendants("span")
                    if holder.has_class("detail-col-chapter")
                    for node in holder.descendants("a")
                ),
                None,
            )
            if anchor is None:
                continue
            moment = _first(row, lambda node: node.has_class("detail-col-updated"))
            title = anchor.text().strip().replace("Ch.", "Chapter")
            title = title[:-3] if title.endswith(".00") else title
            found = _MANHWASME_NUMBER.search(title)
            result.append(
                SourceChapter(
                    source_id=urlparse(urljoin(base, anchor.attrs.get("href", ""))).path.lstrip("/"),
                    title=title,
                    series_id=series_id,
                    source_name=self.name,
                    number=float(found.group()) if found else None,
                    language=self.language,
                    uploaded_at=self._date(moment.text() if moment is not None else ""),
                )
            )
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else str(chapter)
        response = await self._request("GET", self._url(chapter_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        base = str(response.url) or self.base_url
        urls = [
            _manhwasme_image(node, base)
            for holder in root.descendants("div")
            if holder.has_class("reader-pages")
            for wrap in holder.descendants()
            if wrap.has_class("img-wrap")
            for node in wrap.descendants("img")
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

    async def _search_page(self, params: list[tuple[str, str]]) -> dict:
        response = await self._request("GET", f"{self.base_url}/search", params=params)
        response.raise_for_status()
        root = _parse_html(response.text)
        base = str(response.url) or self.base_url
        items: list[SourceSeries] = []
        for grid in root.descendants("div"):
            if not grid.has_class("results-grid"):
                continue
            for card in grid.descendants("a"):
                if not card.has_class("result-card"):
                    continue
                heading = _first(card, lambda node: node.has_class("result-card-title"))
                if heading is None:
                    continue
                cover = next(
                    (
                        node
                        for holder in card.descendants("div")
                        if holder.has_class("result-card-image")
                        for node in holder.descendants("img")
                    ),
                    None,
                )
                items.append(
                    SourceSeries(
                        source_id=urlparse(urljoin(base, card.attrs.get("href", ""))).path.lstrip("/"),
                        title=heading.text().strip(),
                        source_name=self.name,
                        cover_url=_manhwasme_image(cover, base) if cover is not None else None,
                        web_url=urljoin(base, card.attrs.get("href", "")),
                    )
                )
        has_more = any(
            anchor.has_class("page-btn")
            and _first(anchor, lambda node: node.has_class("fa-chevron-right")) is not None
            for holder in root.descendants()
            if holder.has_class("pagination")
            for anchor in holder.descendants("a")
        )
        return {"items": items, "has_more": has_more}

    def _url(self, path: str) -> str:
        # El Kotlin reescribe sobre `/manhwa/...`; aqui el id viaja sin la barra.
        return urljoin(f"{self.base_url}/", f"/{path.lstrip('/')}".replace("/manhwa/", "/manga/"))

    @staticmethod
    def _stat_row(root: _Node, label: str) -> _Node | None:
        for row in root.descendants("div"):
            if not row.has_class("detail-stat-row"):
                continue
            marker = _first(
                row,
                lambda node: node.tag == "span"
                and node.has_class("detail-stat-label")
                and label in node.text().casefold(),
            )
            if marker is not None:
                return row
        return None

    @classmethod
    def _stat(cls, root: _Node, label: str) -> str | None:
        row = cls._stat_row(root, label)
        if row is None:
            return None
        value = _first(row, lambda node: node.tag == "span" and node.has_class("detail-stat-value"))
        return value.text().strip() or None if value is not None else None

    @classmethod
    def _stat_links(cls, root: _Node, label: str) -> list[str]:
        row = cls._stat_row(root, label)
        if row is None:
            return []
        return [
            text
            for value in row.descendants("span")
            if value.has_class("detail-stat-value")
            for node in value.descendants("a")
            if (text := node.text().strip())
        ]

    @staticmethod
    def _status(value: str) -> str | None:
        return {
            "en curso": "ongoing", "completado": "completed",
            "en pausa": "hiatus", "cancelado": "cancelled",
        }.get(value.strip().casefold())

    @staticmethod
    def _date(value: str) -> str | None:
        from datetime import datetime, timedelta

        if "hace" in value.casefold():
            digits = "".join(re.findall(r"\d", value))
            if not digits:
                return None
            amount, lowered = int(digits), value.casefold()
            now = datetime.now().replace(microsecond=0)
            for words, unit in _MANHWASME_UNITS:
                if any(word in lowered for word in words):
                    return (now - timedelta(**{unit: amount})).isoformat()
            if "mes" in lowered:
                return (now - timedelta(days=30 * amount)).isoformat()
            if "año" in lowered:
                return (now - timedelta(days=365 * amount)).isoformat()
            return None
        found = _MANHWASME_DATE.search(value)
        if not found:
            return None
        try:
            return datetime(2000 + int(found.group(3)), int(found.group(2)), int(found.group(1))).isoformat()
        except ValueError:
            return None


class GeneratedManhwasMeSource(ManhwasMeSource):
    name = 'tumanhwasclub_es'
    display_name = 'ManhwasMe'
    base_url = 'https://manhwas.me'
    language = 'es'
    requests_per_minute = 60
    content_warning = 'nsfw'
    image_headers = {'Referer': 'https://manhwas.me/'}


SOURCE = TumanhwasclubSource
