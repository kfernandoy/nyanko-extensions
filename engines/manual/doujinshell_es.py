try:
    from .madara import MadaraSource, _Node, _TreeParser
except ImportError:
    pass

class MadaraSource:
    pass



class DoujinsHellSource(MadaraSource):
    def get_filters(self) -> list[SourceFilter]:
        return [
            SourceFilter("author", "Autor", "text", default=""),
            SourceFilter("artist", "Artista", "text", default=""),
            SourceFilter("year", "Ano de publicacion", "text", default=""),
            SourceFilter("status", "Estado", "multi_select", [
                ("end", "Completado"), ("on-going", "En curso"),
                ("canceled", "Cancelado"), ("on-hold", "En espera"),
            ], []),
            SourceFilter("order", "Ordenar por", "select", [
                ("", "Relevancia"), ("latest", "Mas recientes"), ("alphabet", "A-Z"),
                ("rating", "Valoracion"), ("trending", "Tendencia"),
                ("views", "Mas vistos"), ("new-manga", "Nuevos"),
            ], ""),
            SourceFilter("adult", "Contenido adulto", "select", [
                ("", "Todo"), ("0", "Excluir"), ("1", "Solo adulto"),
            ], ""),
        ]

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        values = filters or {}
        path = "" if page == 1 else f"page/{page}/"
        params: list[tuple[str, str]] = [("s", query), ("post_type", "wp-manga")]
        for key, parameter in (("author", "author"), ("artist", "artist"), ("year", "release")):
            if str(values.get(key, "")).strip():
                params.append((parameter, str(values[key]).strip()))
        statuses = values.get("status", [])
        if isinstance(statuses, list):
            params.extend(("status[]", str(status)) for status in statuses)
        if values.get("order"):
            params.append(("m_orderby", str(values["order"])))
        params.append(("adult", str(values.get("adult", ""))))
        response = await self._request("GET", urljoin(f"{self.base_url}/", path), params=params)
        response.raise_for_status()
        root = _parse_html(response.text)
        items = self._series_from_root(root, ("c-tabs-item__content", "manga__item"))
        has_more = any(
            node.attrs.get("rel") == "next" or node.has_class("nextpostslink")
            or node.has_class("nav-previous")
            for node in root.descendants()
        )
        return {"items": items, "has_more": has_more}

    @staticmethod
    def _doujinshell_date(value: str) -> str | None:
        from datetime import datetime
        months = {
            "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
            "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10,
            "noviembre": 11, "diciembre": 12,
        }
        found = re.fullmatch(r"(\d{1,2})\s+([^,]+),\s*(\d{4})", value.strip().lower())
        if not found or found.group(2) not in months:
            return None
        return datetime(int(found.group(3)), months[found.group(2)], int(found.group(1))).isoformat()

    @classmethod
    def _doujinshell_chapter_nodes(cls, root):
        return [
            node for node in root.descendants("li")
            if node.has_class("wp-manga-chapter") and cls._has_class_ancestor(node, "listing-chapters_wrap")
        ]

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        series_url = urljoin(f"{self.base_url}/", series_id)
        response = await self._request("GET", series_url)
        response.raise_for_status()
        root = _parse_html(response.text)
        items = self._doujinshell_chapter_nodes(root)
        holder = _first(root, lambda node: node.attrs.get("id", "").startswith("manga-chapters-holder"))
        if not items and holder is not None:
            chapter_response = await self._request(
                "POST", f"{self.base_url}/wp-admin/admin-ajax.php",
                data={"action": "manga_get_chapters", "manga": holder.attrs.get("data-id", "")},
            )
            if getattr(chapter_response, "status_code", 200) == 400:
                chapter_response = await self._request("POST", f"{series_url.rstrip('/')}/ajax/chapters")
            chapter_response.raise_for_status()
            items = self._doujinshell_chapter_nodes(_parse_html(chapter_response.text))
        result = []
        for item in items:
            anchor = _first(item, lambda node: node.tag == "a" and bool(node.attrs.get("href")))
            if anchor is None:
                continue
            title = anchor.text().strip()
            date = _first(item, lambda node: node.tag == "span" and node.has_class("chapter-release-date"))
            found = re.search(r"\d+(?:\.\d+)?", title)
            chapter_url = urljoin(series_url, anchor.attrs["href"]).split("?style=paged", 1)[0]
            if not chapter_url.endswith(self.chapter_url_suffix):
                chapter_url += self.chapter_url_suffix
            result.append(SourceChapter(
                source_id=chapter_url, title=title, series_id=series_id, source_name=self.name,
                number=float(found.group()) if found else None, language=self.language,
                uploaded_at=self._doujinshell_date(date.text()) if date else None,
            ))
        if len(result) == 1:
            only = result[0]
            result[0] = SourceChapter(
                source_id=only.source_id, title="Cap\u00edtulo", series_id=only.series_id,
                source_name=only.source_name, number=only.number, language=only.language,
                uploaded_at=only.uploaded_at,
            )
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        response = await self._request("GET", urljoin(f"{self.base_url}/", chapter_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        reading = _first(root, lambda node: node.has_class("reading-content"))
        images = [image for image in reading.descendants("img") if not image.has_class("aligncenter")] if reading else []
        if not images and reading and reading.descendants("iframe"):
            raise ValueError("No se admiten videos")
        urls = [_image_url(image, str(response.url)) for image in images]
        return [SourcePage(
            source_id=url, chapter_id=chapter_id, index=index,
            filename=urlparse(url).path.rsplit("/", 1)[-1] or f"{index}.jpg", source_name=self.name,
        ) for index, url in enumerate(urls)]
class GeneratedMadaraSource(DoujinsHellSource):
    name = 'doujinshell_es'
    display_name = 'DoujinsHell'
    base_url = 'https://doujinshell.net'
    language = 'es'
    manga_substring = 'doujin'
    load_more = 'never'
    use_new_chapter_endpoint = False
    chapter_url_suffix = '?style=list'
    supports_latest = True
    requests_per_minute = 60
    pages_profile = 'default'
    extra_headers = {}
    image_headers = {}
    date_format = 'd MMMM, yyyy'
    date_locale = 'es'
    details_profile = 'default'
    content_warning = 'nsfw'

SOURCE = GeneratedMadaraSource
