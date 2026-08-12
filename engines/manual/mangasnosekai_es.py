try:
    from .madara import MadaraSource, _Node, _TreeParser
except ImportError:
    pass

class MadaraSource:
    pass



class MangasNoSekaiSource(MadaraSource):
    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"}:
            return []
        suffix = "" if page == 1 else f"page/{page}/"
        response = await self._request(
            "GET",
            f"{self.base_url}/biblioteca/{suffix}",
            params={"m_orderby": "views" if kind == "popular" else "latest"},
        )
        response.raise_for_status()
        result = []
        for item in _parse_html(response.text).descendants("div"):
            parent = item.parent
            if not (
                parent is not None and parent.has_class("row")
                and parent.parent is not None and parent.parent.has_class("page-listing-item")
            ):
                continue
            anchor = _first(item, lambda node: node.tag == "a" and bool(node.attrs.get("href")))
            title = _first(item, lambda node: node.tag == "figcaption")
            if anchor is None or title is None:
                continue
            image = _first(item, lambda node: node.tag == "img")
            url = urljoin(str(response.url), anchor.attrs["href"])
            result.append(SourceSeries(
                source_id=url, title=title.text().strip(), source_name=self.name,
                cover_url=_image_url(image, str(response.url)) if image else None, web_url=url,
            ))
        return result

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        synopsis = _first(root, lambda node: node.tag == "section" and node.attrs.get("id") == "section-sinopsis")

        def row(label: str) -> _Node | None:
            if synopsis is None:
                return None
            return _first(
                synopsis,
                lambda node: node.tag == "div" and node.has_class("d-flex")
                and any(child.tag == "div" and label in child.text() for child in node.descendants("div")),
            )

        def value(label: str) -> _Node | None:
            item = row(label)
            return _first(item, lambda node: node.tag == "p") if item else None

        title = _first(root, lambda node: node.tag == "p" and node.has_class("titleMangaSingle"))
        image = _first(
            root,
            lambda node: node.tag == "img" and node.has_class("img-responsive")
            and self._has_class_ancestor(node, "thumble-container"),
        )
        description = next(
            (child for child in synopsis.children if isinstance(child, _Node) and child.tag == "p"),
            None,
        ) if synopsis else None
        author = value("Autor")
        status = value("Estado")
        genre = value("Generos")
        alt_name = value("Otros nombres")
        description_text = description.text().strip() if description else ""
        if alt_name and alt_name.text().strip() and "updating" not in alt_name.text().casefold():
            description_text = f"{description_text}\n\nOtros nombres: {alt_name.text().strip()}".strip()
        genres = tuple(
            anchor.text().strip().capitalize()
            for anchor in genre.descendants("a") if anchor.text().strip()
        ) if genre else ()
        return SourceSeries(
            source_id=series_id,
            title=title.text().strip() if title else series.title if isinstance(series, SourceSeries) else series_id.rstrip("/").rsplit("/", 1)[-1],
            source_name=self.name,
            cover_url=_image_url(image, str(response.url)) if image else None,
            description=description_text or None,
            author=", ".join(anchor.text().strip() for anchor in author.descendants("a") if anchor.text().strip()) if author else None,
            status=self._madara_status(status.text() if status else ""),
            content_tags=genres,
            metadata=series.metadata if isinstance(series, SourceSeries) else {},
            web_url=str(response.url),
        )

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        series_url = urljoin(f"{self.base_url}/", series_id)
        response = await self._request("GET", series_url)
        response.raise_for_status()
        root = _parse_html(response.text)
        script = _first(root, lambda node: node.tag == "script" and node.attrs.get("id") == "wp-manga-js")
        if script is None or not script.attrs.get("src"):
            raise ValueError("No se pudo obtener el script de capítulos")
        script_response = await self._request("GET", urljoin(str(response.url), script.attrs["src"]))
        script_response.raise_for_status()
        endpoint, fields = self._ajax_config(script_response.text)
        extra = _first(root, lambda node: node.tag == "script" and node.attrs.get("id") == "wp-manga-js-extra")
        fallback = _first(root, lambda node: node.tag == "script" and node.attrs.get("id") == "manga_disqus_embed-js-extra")
        manga_id = re.search(r'''["']manga_id["']\s*:\s*["']([^"']+)''', extra.text() if extra else "")
        manga_id = manga_id or re.search(r'''["']postId["']\s*:\s*["']([^"']+)''', fallback.text() if fallback else "")
        if manga_id is None:
            raise ValueError("No se pudo obtener el id del manga")

        result = []
        page = 1
        while True:
            chapter_response = await self._request(
                "POST", urljoin(f"{self.base_url}/", endpoint),
                data={"mangaid": manga_id.group(1), "page": str(page), **fields},
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
            chapter_response.raise_for_status()
            payload = chapter_response.json() if hasattr(chapter_response, "json") else json.loads(chapter_response.text)
            for item in payload.get("chapters_to_display", []):
                title_text = str(item.get("name", "")).strip()
                number = re.search(r"\d+(?:\.\d+)?", title_text)
                date_text = _parse_html(str(item.get("date", ""))).text()
                result.append(SourceChapter(
                    source_id=urljoin(series_url, str(item.get("link", ""))).rstrip("/"),
                    title=title_text or "Capítulo", series_id=series_id, source_name=self.name,
                    number=float(number.group()) if number else None, language=self.language,
                    uploaded_at=self._madara_date(date_text),
                ))
            if int(payload.get("current_page", page)) >= int(payload.get("total_pages", page)):
                return result
            page += 1

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else str(chapter)
        return await super().pages(f"{chapter_id.rstrip('/')}/")

    @staticmethod
    def _ajax_config(script: str) -> tuple[str, dict[str, str]]:
        array = re.search(r"\b(?:var|let|const)\s+(\w+)\s*=\s*(\[.*?])\s*;", script, re.S)
        variants = [script]
        if array:
            try:
                values = ast.literal_eval(array.group(2))
            except (SyntaxError, ValueError):
                values = []
            for function in re.finditer(r"(?:\b(?:var|let|const)\s+)?(\w+)\s*=\s*function\s*\((\w+)[^)]*\)\s*\{(.*?)\}", script, re.S):
                decoder, argument, body = function.groups()
                offset = re.search(rf"\b{re.escape(argument)}\s*=\s*{re.escape(argument)}\s*-\s*(0x[\da-f]+|\d+)", body, re.I)
                if not values or not offset or not re.search(rf"\b{re.escape(array.group(1))}\s*\[", body):
                    continue
                base = int(offset.group(1), 0)
                calls = re.compile(rf"\b{re.escape(decoder)}\(\s*(['\"])(0x[\da-f]+|\d+)\1[^)]*\)", re.I)
                for shift in range(len(values)):
                    rotated = values[shift:] + values[:shift]
                    variants.append(calls.sub(
                        lambda match: json.dumps(rotated[int(match.group(2), 0) - base])
                        if 0 <= int(match.group(2), 0) - base < len(rotated) else match.group(),
                        script,
                    ))
        pattern = re.compile(
            r'''function\s+.*?\.ajax\b.*?['"]?url['"]?\s*:\s*(['"])(.*?)\1(?:.*?['"]?data['"]?\s*:\s*\{(.*?)\})?''',
            re.S,
        )
        for candidate in variants:
            found = pattern.search(candidate)
            if found and (found.group(2).startswith("/") or found.group(2).startswith("http")):
                fields = {
                    item.group(1): item.group(3)
                    for item in re.finditer(r'''['"]?(\w+)['"]?\s*:\s*(['"])(.*?)\2''', found.group(3) or "")
                }
                return found.group(2), fields
        raise ValueError("No se pudo obtener el endpoint de capítulos")
class GeneratedMadaraSource(MangasNoSekaiSource):
    name = 'mangasnosekai_es'
    display_name = 'Mangas No Sekai'
    base_url = 'https://mangasnosekai.com'
    language = 'es'
    manga_substring = 'manga'
    load_more = 'never'
    use_new_chapter_endpoint = True
    chapter_url_suffix = '?style=list'
    supports_latest = True
    requests_per_minute = 120
    pages_profile = 'default'
    extra_headers = {}
    image_headers = {}
    date_format = 'MMMM dd, yyyy'
    date_locale = 'es'
    details_profile = 'default'
    content_warning = 'safe'

SOURCE = GeneratedMadaraSource
