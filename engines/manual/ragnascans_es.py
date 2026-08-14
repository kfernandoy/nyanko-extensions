try:
    from .base import (
        FuenteBaseSource, _Node, _TreeParser
    )
except ImportError:
    pass

class FuenteBaseSource:
    pass


class RagnascansSource(FuenteBaseSource):
    """Las paginas del lector llegan en base64 invertido dentro de data-verify."""

    def get_filters(self) -> list[SourceFilter]:
        return [
            SourceFilter("generos", "Géneros", "multi_select", [
                (value, value) for value in _RAGNA_GENRES
            ], []),
            SourceFilter("estado", "Estado", "multi_select", [
                ("emision", "En emisión"), ("finalizado", "Finalizado"), ("hiatus", "Hiatus"),
                ("pausado", "Pausado"), ("cancelado", "Cancelado"),
            ], []),
            SourceFilter("tipo", "Tipo", "multi_select", [
                ("manhwa", "Manhwa"), ("manga", "Manga"),
                ("manhua", "Manhua"), ("novela", "Novela"),
            ], []),
            SourceFilter("orden", "Ordenar por", "select", [
                ("actualizado", "Más recientes"), ("vistas", "Más populares"),
                ("votos", "Mejor valorados"), ("az", "A — Z"), ("za", "Z — A"),
                ("nuevo", "Recién agregados"),
            ], "vistas"),
        ]

    async def browse(self, kind: str, page: int = 1):
        if kind not in {"popular", "latest"}:
            return {"items": [], "has_more": False}
        return await self._directory([
            ("page", str(page)),
            ("orden", "vistas" if kind == "popular" else "actualizado"),
            ("q", ""),
        ])

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        query = query.strip()
        if query.startswith(("http://", "https://")):
            # Pegar la URL de una serie la abre directamente, como en el Kotlin.
            if urlparse(query).netloc == urlparse(self.base_url).netloc:
                return {"items": [await self.details(self._path(query, self.base_url))], "has_more": False}
        values = filters or {}
        params: list[tuple[str, str]] = [("page", str(page)), ("q", query)]
        for key, parameter in (("generos", "generos[]"), ("estado", "estado[]"), ("tipo", "tipo[]")):
            chosen = values.get(key)
            if isinstance(chosen, list):
                params.extend((parameter, str(value)) for value in chosen)
        params.append(("orden", str(values.get("orden") or "vistas")))
        return await self._directory(params)

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        base = str(response.url) or self.base_url
        title = _first(root, lambda node: node.tag == "h1")
        image = next(
            (
                node
                for holder in root.descendants("div")
                if holder.has_class("cover-wrapper")
                for node in holder.descendants("img")
            ),
            None,
        )
        summary = next(
            (
                node.text().strip()
                for holder in root.descendants()
                if holder.attrs.get("id") == "sinopsisWrapper"
                for node in holder.descendants("p")
            ),
            None,
        )
        rows = self._meta_rows(root)
        return SourceSeries(
            source_id=series_id,
            title=title.text().strip() if title is not None else "",
            source_name=self.name,
            cover_url=urljoin(base, image.attrs.get("src", "")) if image is not None else None,
            description=summary or None,
            author=self._info(root, "Autor:"),
            artist=self._info(root, "Ilustrador:"),
            status=self._status(rows.get("estado")),
            content_tags=tuple(
                value for part in (rows.get("género") or "").split(",") if (value := part.strip())
            ),
            web_url=urljoin(f"{self.base_url}/", series_id),
        )

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        base = str(response.url) or self.base_url
        result: list[SourceChapter] = []
        for holder in root.descendants():
            if holder.attrs.get("id") != "chaptersContainer":
                continue
            for item in holder.descendants():
                if not item.has_class("chapter-item"):
                    continue
                # Los capitulos de pago vienen bloqueados y no se listan.
                locked = item.has_class("locked-neon") or _first(
                    item, lambda node: node.has_class("ph-lock-key"),
                ) is not None
                heading = next(
                    (
                        node
                        for label in item.descendants()
                        if label.has_class("chapter-item-title")
                        for node in label.descendants("h4")
                    ),
                    None,
                )
                if locked or heading is None:
                    continue
                moment = _first(item, lambda node: node.has_class("chapter-item-date"))
                title = heading.text().strip()
                title = title[:-3] if title.endswith(".00") else title
                found = _RAGNA_NUMBER.search(title)
                result.append(
                    SourceChapter(
                        source_id=self._path(item.attrs.get("href", ""), base),
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
        response = await self._request("GET", urljoin(f"{self.base_url}/", chapter_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        base = str(response.url) or self.base_url
        urls: list[str] = []
        for holder in root.descendants():
            if holder.attrs.get("id") != "pagesContainer":
                continue
            for container in holder.descendants():
                if not container.has_class("page-container"):
                    continue
                for node in container.descendants("img"):
                    value = self._image(node, base)
                    if value:
                        urls.append(value)
        return [
            SourcePage(
                source_id=value,
                chapter_id=chapter_id,
                index=index,
                filename=urlparse(value).path.rsplit("/", 1)[-1] or f"{index}.jpg",
                source_name=self.name,
            )
            for index, value in enumerate(urls)
        ]

    async def _directory(self, params: list[tuple[str, str]]) -> dict:
        response = await self._request("GET", f"{self.base_url}/directorio.php", params=params)
        response.raise_for_status()
        root = _parse_html(response.text)
        base = str(response.url) or self.base_url
        items: list[SourceSeries] = []
        for grid in root.descendants():
            if not grid.has_class("mod-grid"):
                continue
            for card in grid.descendants():
                if not card.has_class("mod-card"):
                    continue
                heading = _first(card, lambda node: node.has_class("mod-card-title"))
                if heading is None:
                    continue
                cover = _first(card, lambda node: node.has_class("mod-card-cover"))
                items.append(
                    SourceSeries(
                        source_id=self._path(card.attrs.get("href", ""), base),
                        title=heading.text().strip(),
                        source_name=self.name,
                        cover_url=urljoin(base, cover.attrs.get("src", "")) if cover is not None else None,
                        web_url=urljoin(base, card.attrs.get("href", "")),
                    )
                )
        has_more = any(
            node.has_class("mod-pg-btn") and "sig" in node.text().casefold()
            for node in root.descendants()
        )
        return {"items": items, "has_more": has_more}

    def _image(self, node: _Node, base: str) -> str:
        verify = node.attrs.get("data-verify", "")
        if verify:
            try:
                value = base64.b64decode(verify + "=" * (-len(verify) % 4)).decode("utf-8", "ignore")[::-1]
            except Exception:
                return ""
            if value.startswith("http"):
                return value
            if value.startswith("//"):
                return f"https:{value}"
            return f"{self.base_url}{value}"
        source = node.attrs.get("src", "")
        return "" if not source or source.startswith("data:image") else urljoin(base, source)

    @staticmethod
    def _path(href: str, base: str) -> str:
        # Los enlaces son "manga.php?id=16": sin la query todas las series colapsan.
        parsed = urlparse(urljoin(base, href))
        return f"{parsed.path.lstrip('/')}{'?' + parsed.query if parsed.query else ''}"

    @staticmethod
    def _meta_rows(root: _Node) -> dict[str, str]:
        result: dict[str, str] = {}
        for table in root.descendants():
            if not table.has_class("meta-table"):
                continue
            for row in table.descendants():
                if not row.has_class("meta-row"):
                    continue
                label = _first(row, lambda node: node.has_class("meta-label"))
                value = _first(row, lambda node: node.has_class("meta-value"))
                if label is None or value is None:
                    continue
                key = label.text().strip().casefold().rstrip(":")
                links = [text for node in value.descendants("a") if (text := node.text().strip())]
                result[key] = ", ".join(links) if links else value.text().strip()
        return result

    @staticmethod
    def _info(root: _Node, label: str) -> str | None:
        for holder in root.descendants("div"):
            classes = set(holder.attrs.get("class", "").split())
            if not {"flex", "flex-wrap", "items-center", "gap-x-3"} <= classes:
                continue
            for node in holder.descendants("span"):
                text = node.text().strip()
                if label in text:
                    return text.split(label, 1)[1].strip() or None
        return None

    @staticmethod
    def _status(value: str | None) -> str | None:
        return {
            "emision": "ongoing", "en emisión": "ongoing", "en emision": "ongoing",
            "finalizado": "completed", "hiatus": "hiatus", "pausado": "hiatus",
            "cancelado": "cancelled",
        }.get((value or "").strip().casefold())

    @staticmethod
    def _date(value: str) -> str | None:
        from datetime import datetime

        found = _RAGNA_DATE.search(value)
        month = _RAGNA_MONTHS.get(found.group(2).casefold()) if found else None
        if month is None:
            return None
        try:
            return datetime(int(found.group(3)), month, int(found.group(1))).isoformat()
        except ValueError:
            return None




SOURCE = RagnascansSource
