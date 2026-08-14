try:
    from .base import FuenteBaseSource, _Node, _TreeParser
except ImportError:
    pass

class FuenteBaseSource:
    pass


class MyreadingmangaSource(FuenteBaseSource):
    """El sitio no publica total de paginas: se acumula lo leido contra ep-search-count."""

    extra_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/136.0.0.0 Mobile Safari/537.36"
        ),
    }

    def __init__(self, fetcher: SourceFetcher | None = None) -> None:
        super().__init__(fetcher)
        self._options: dict[str, list[tuple[str, str]]] | None = None
        self._parsed_so_far = 0

    @property
    def site_language(self) -> str:
        return _MRM_LANGS.get(self.language, self.language)

    @property
    def latest_language(self) -> str:
        return "jp" if self.language == "ja" else self.site_language

    async def get_filters(self) -> list[SourceFilter]:
        options = await self._filter_options()
        result = [
            SourceFilter("enforce_lang", "Enforce language", "checkbox", default=True),
            SourceFilter("sort", "Sort by", "select", list(_MRM_SORT), "date"),
        ]
        result.extend(
            SourceFilter(
                identifier, label, "select", [("", "Any")] + options.get(identifier, []), "",
            )
            for identifier, label, _, _, _ in _MRM_DYNAMIC
        )
        return result

    async def browse(self, kind: str, page: int = 1):
        if kind == "popular":
            # "Populares" es en realidad el listado aleatorio del buscador.
            response = await self._request(
                "GET",
                f"{self.base_url}/page/{page}/",
                params={"s": "", "ep_sort": "rand", "ep_filter_lang": self.site_language},
            )
            response.raise_for_status()
            return self._search_results(response, page)
        if kind == "latest":
            suffix = f"/page/{page}/" if page > 1 else ""
            response = await self._request(
                "GET", f"{self.base_url}/lang/{self.latest_language.lower()}{suffix}",
            )
            response.raise_for_status()
            root = _parse_html(response.text)
            base = str(response.url) or self.base_url
            return {
                "items": self._articles(root, base),
                "has_more": any(
                    node.has_class("pagination-next") for node in root.descendants("li")
                ),
            }
        return {"items": [], "has_more": False}

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        values = filters or {}
        params: list[tuple[str, str]] = [("s", query)]
        if values.get("enforce_lang", True):
            params.append(("ep_filter_lang", self.site_language))
        params.append(("ep_sort", str(values.get("sort", "date"))))
        params.extend(
            (parameter, str(values[identifier]))
            for identifier, _, parameter, _, _ in _MRM_DYNAMIC
            if str(values.get(identifier) or "")
        )
        response = await self._request("GET", f"{self.base_url}/page/{page}/", params=params)
        response.raise_for_status()
        return self._search_results(response, page)

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        heading = _first(root, lambda node: node.tag == "h1")
        raw = heading.text().strip() if heading is not None else ""
        scanlators = [
            text
            for holder in root.descendants("div")
            if holder.has_class("entry-terms")
            for node in holder.descendants("a")
            if "group" in node.attrs.get("href", "") and (text := node.text().strip())
        ]
        extended = [
            text
            for holder in root.descendants("div")
            if holder.has_class("entry-content")
            for node in holder.descendants("p")
            if "|" not in node.text() and (text := node.text().strip())
        ]
        description = "\n".join(
            part
            for part in (
                raw,
                f"Scanlated by: {', '.join(scanlators)}" if scanlators else None,
                "\n".join(extended) if extended else None,
            )
            if part
        ).strip()
        status = _first(root, lambda node: node.tag == "a" and "status" in node.attrs.get("href", ""))
        known = series if isinstance(series, SourceSeries) else None
        return SourceSeries(
            source_id=series_id,
            title=self._clean_title(raw),
            source_name=self.name,
            cover_url=known.cover_url if known else None,
            description=description or None,
            author=self._clean_author(raw) or None,
            artist=self._clean_author(raw) or None,
            status={"Ongoing": "ongoing", "Completed": "completed"}.get(
                status.text().strip() if status is not None else "",
            ),
            content_tags=tuple(self._genres(root)),
            web_url=urljoin(f"{self.base_url}/", series_id),
        )

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        moment = _first(root, lambda node: node.has_class("entry-time"))
        stamp = self._date(moment.text() if moment is not None else "")
        # El paginador esconde tramos: se toma el ultimo numero y se rellena hasta el.
        numbers = [
            value
            for node in root.descendants("a")
            if node.attrs.get("class", "").strip() == "page-numbers"
            and (value := self._int(node.text().strip())) is not None
        ]
        last = numbers[-1] if numbers else 1
        path = series_id.rstrip("/")
        return [
            SourceChapter(
                source_id=f"{path}/{number}",
                title=f"Part {number}",
                series_id=series_id,
                source_name=self.name,
                number=float(number),
                language=self.language,
                uploaded_at=stamp,
            )
            for number in range(last, 0, -1)
        ]

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else str(chapter)
        response = await self._request("GET", urljoin(f"{self.base_url}/", chapter_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        base = str(response.url) or self.base_url
        found: list[_Node] = []
        for holder in root.descendants("div"):
            if holder.has_class("entry-content"):
                found.extend(holder.descendants("img"))
            elif holder.has_class("separator"):
                found.extend(
                    node for node in holder.descendants("img") if node.attrs.get("data-src")
                )
        urls: list[str] = []
        for node in found:
            value = self._image(node, base)
            if value and value not in urls:
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

    # -------------------------------------------------------------- internals
    async def _filter_options(self) -> dict[str, list[tuple[str, str]]]:
        if self._options is not None:
            return self._options
        options: dict[str, list[tuple[str, str]]] = {}
        for identifier, _, _, path, holder_class in _MRM_DYNAMIC:
            try:
                response = await self._request("GET", f"{self.base_url}/{path}")
                response.raise_for_status()
                root = _parse_html(response.text)
            except Exception:
                continue
            found: list[tuple[str, str]] = []
            for holder in root.descendants():
                if not holder.has_class(holder_class):
                    continue
                for anchor in holder.descendants("a"):
                    href = anchor.attrs.get("href", "")
                    if identifier == "genre" and "/genre/" not in href:
                        continue
                    parts = [part for part in href.split("/")[:-1]]
                    found.append((parts[-1] if parts else "", anchor.text().strip()))
            if found:
                options[identifier] = found
        self._options = options
        return options

    def _search_results(self, response: Any, page: int) -> dict:
        root = _parse_html(response.text)
        base = str(response.url) or self.base_url
        items = self._articles(root, base)
        if page == 1:
            self._parsed_so_far = 0
        self._parsed_so_far += len(items)
        counter = _first(root, lambda node: node.has_class("ep-search-count"))
        found = _MRM_TOTAL.search(counter.text() if counter is not None else "")
        total = self._int(found.group(1).replace(",", "")) if found else 0
        return {"items": items, "has_more": self._parsed_so_far < (total or 0)}

    def _articles(self, root: _Node, base: str) -> list[SourceSeries]:
        result: list[SourceSeries] = []
        for article in root.descendants("article"):
            anchor = _first(article, lambda node: node.tag == "a" and "rel" in node.attrs)
            if anchor is None:
                continue
            image = next(
                (
                    node
                    for holder in article.descendants("a")
                    if holder.has_class("entry-image-link")
                    for node in holder.descendants("img")
                ),
                None,
            )
            result.append(
                SourceSeries(
                    source_id=urlparse(urljoin(base, anchor.attrs.get("href", ""))).path.lstrip("/"),
                    title=self._clean_title(anchor.text()),
                    source_name=self.name,
                    cover_url=self._thumbnail(self._image(image, base)) if image is not None else None,
                    web_url=urljoin(base, anchor.attrs.get("href", "")),
                )
            )
        return result

    @staticmethod
    def _genres(root: _Node) -> list[str]:
        result: list[str] = []
        for holder in root.descendants("div"):
            if not holder.has_class("entry-header"):
                continue
            result.extend(
                text
                for node in holder.descendants("p")
                for anchor in node.descendants("a")
                if "genre" in anchor.attrs.get("href", "") and (text := anchor.text().strip())
            )
        # El segundo selector del Kotlin no esta acotado al encabezado.
        result.extend(
            text
            for node in root.descendants()
            if "tag" in node.attrs.get("href", "") and (text := node.text().strip())
        )
        result.extend(
            text
            for holder in root.descendants("span")
            if holder.has_class("entry-categories")
            for node in holder.descendants("a")
            if (text := node.text().strip())
        )
        return list(dict.fromkeys(result))

    @staticmethod
    def _image(node: _Node, base: str) -> str:
        for attribute in ("data-src", "data-cfsrc", "src"):
            value = node.attrs.get(attribute, "")
            if _MRM_EXTENSION.search(value):
                return urljoin(base, value)
        value = urljoin(base, node.attrs.get("data-lazy-src", ""))
        return value if urlparse(value).netloc else ""

    @staticmethod
    def _thumbnail(value: str) -> str | None:
        # Quita el sufijo de redimension: "foto-300x400.jpg" -> "foto.jpg".
        if not value:
            return None
        return f"{value.rsplit('-', 1)[0]}.{value.rsplit('.', 1)[-1]}"

    @staticmethod
    def _clean_title(value: str) -> str:
        cleaned = _MRM_TITLE.sub("", value)
        return cleaned.rsplit("(", 1)[0].strip() if "(" in cleaned else cleaned.strip()

    @staticmethod
    def _clean_author(value: str) -> str:
        return value.partition("[")[2].partition("]")[0].strip()

    @staticmethod
    def _int(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _date(value: str) -> str | None:
        from datetime import datetime

        found = _MRM_DATE.search(value or "")
        month = _MRM_MONTHS.get(found.group(1).casefold()) if found else None
        if month is None:
            return None
        try:
            return datetime(int(found.group(3)), month, int(found.group(2))).isoformat()
        except ValueError:
            return None


class GeneratedMyReadingMangaSource(MyReadingMangaSource):
    name = 'myreadingmanga_es'
    display_name = 'MyReadingManga'
    base_url = 'https://myreadingmanga.info'
    language = 'es'
    requests_per_minute = 60
    content_warning = 'nsfw'
    image_headers = {'Referer': 'https://myreadingmanga.info/'}


SOURCE = MyreadingmangaSource
