try:
    from .madara import MadaraSource, _Node, _TreeParser
except ImportError:
    pass

class MadaraSource:
    pass



class HaremDeKiraSource(MadaraSource):
    async def browse(self, kind: str, page: int = 1):
        if kind not in {"popular", "latest"}:
            return {"items": [], "has_more": False}
        response = await self._request(
            "POST", f"{self.base_url}/wp-admin/admin-ajax.php",
            data=self._harem_load_more(page, kind == "popular"),
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        response.raise_for_status()
        return self._harem_page(response, search=False)

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        query = query.strip()
        if query.startswith("https://"):
            parsed = urlparse(query)
            if parsed.netloc != urlparse(self.base_url).netloc:
                raise ValueError("URL no compatible")
            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) < 2:
                raise ValueError("URL no compatible")
            query = f"slug:{parts[1]}"
        if query.startswith("slug:"):
            response = await self._request("GET", f"{self.base_url}/{self.manga_substring}/{query[5:]}/")
            response.raise_for_status()
            return {"items": [self._harem_details(response)], "has_more": False}
        response = await self._request(
            "POST", f"{self.base_url}/wp-admin/admin-ajax.php",
            data=self._harem_search_load_more(page, query, filters or {}),
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        response.raise_for_status()
        return self._harem_page(response, search=True)

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        return self._harem_details(response)

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        result: list[SourceChapter] = []
        for anchor in root.descendants("a"):
            if not (anchor.parent and anchor.parent.tag == "li" and self._has_id_ancestor(anchor, "list-chapters")):
                continue
            grid = _first(anchor, lambda node: node.tag == "div" and node.has_class("grid"))
            if grid is None:
                continue
            title_node = next(
                (child for child in grid.children if isinstance(child, _Node) and child.tag == "span"),
                None,
            )
            date_node = next(
                (child for child in grid.children if isinstance(child, _Node) and child.tag == "div"),
                None,
            )
            title = title_node.text().strip() if title_node else "Capítulo"
            number = re.search(r"\d+(?:\.\d+)?", title)
            result.append(SourceChapter(
                source_id=urljoin(str(response.url), anchor.attrs.get("href", "")),
                title=title,
                series_id=str(series_id),
                source_name=self.name,
                number=float(number.group()) if number else None,
                language=self.language,
                uploaded_at=self._madara_date(date_node.text()) if date_node else None,
            ))
        return result

    def _harem_page(self, response, search: bool) -> dict:
        root = _parse_html(response.text)
        containers = [
            node for node in root.descendants("div")
            if (
                search and node.has_class("grid") and node.parent is not None
                and node.parent.tag == "button" and node.parent.has_class("group")
            ) or (not search and node.has_class("latest-poster"))
        ]
        items: list[SourceSeries] = []
        for container in containers:
            title = _first(container, lambda node: node.tag == "h3" and bool(node.text().strip()))
            anchor = _first(container, lambda node: node.tag == "a" and bool(node.attrs.get("href")))
            styled = _first(
                container,
                lambda node: bool(node.attrs.get("style")) and node.has_class("bg-cover")
                and (node.tag == "div" if search else node.tag == "a"),
            )
            if title is None or anchor is None:
                continue
            source_id = urljoin(str(response.url), anchor.attrs["href"])
            items.append(SourceSeries(
                source_id=source_id,
                title=title.text().strip(),
                source_name=self.name,
                cover_url=self._style_image(styled.attrs["style"], str(response.url)) if styled else None,
                web_url=source_id,
            ))
        return {
            "items": items,
            "has_more": not any(node.has_class("no-posts") for node in root.descendants()),
        }

    def _harem_details(self, response) -> SourceSeries:
        root = _parse_html(response.text)
        title = _first(
            root,
            lambda node: node.tag == "h1" and node.parent is not None and node.parent.has_class("grid")
            and self._has_class_ancestor(node, "wp-manga"),
        )
        typed = [
            node for node in root.descendants("div")
            if node.attrs.get("alt") == "type" and self._has_class_ancestor(node, "wp-manga")
        ]
        status_node = _first(typed[0], lambda node: node.tag == "span") if typed else None
        genres = tuple(
            text for node in typed[1:]
            if (span := _first(node, lambda item: item.tag == "span")) is not None
            and (text := span.text().strip())
        )
        description = _first(
            root,
            lambda node: node.tag == "div" and node.attrs.get("id") == "expand_content"
            and self._has_class_ancestor(node, "wp-manga"),
        )
        paragraphs = description.descendants("p") if description else []
        description_text = (
            "\n\n".join(node.text().strip() for node in paragraphs if node.text().strip())
            if paragraphs else description.text().strip() if description else ""
        )
        image = _first(root, lambda node: node.tag == "img" and self._has_class_ancestor(node, "summary_image"))
        authors = self._detail_links(root, ("author-content", "manga-authors"))
        artists = self._detail_links(root, ("artist-content",))
        source_id = str(response.url)
        return SourceSeries(
            source_id=source_id,
            title=title.text().strip() if title else source_id.rstrip("/").rsplit("/", 1)[-1],
            source_name=self.name,
            cover_url=_image_url(image, source_id) if image else None,
            description=description_text or None,
            author=", ".join(authors) or None,
            artist=", ".join(artists) or None,
            status=self._madara_status(status_node.text() if status_node else ""),
            content_tags=genres,
            web_url=source_id,
        )

    @staticmethod
    def _style_image(style: str, base_url: str) -> str | None:
        found = re.search(r"url\((.*?)\)", style)
        return urljoin(base_url, found.group(1).strip(" '\"")) if found else None

    @staticmethod
    def _harem_load_more(page: int, popular: bool) -> list[tuple[str, str]]:
        return [
            ("action", "madara_load_more"), ("page", str(page - 1)),
            ("template", "madara-core/content/content-archive"),
            ("vars[orderby]", "meta_value_num"), ("vars[paged]", "1"),
            ("vars[meta_query][0][key]", "_wp_manga_chapter_type"),
            ("vars[meta_query][0][value]", "manga"),
            ("vars[post_type]", "wp-manga"), ("vars[post_status]", "publish"),
            ("vars[meta_key]", "_wp_manga_views" if popular else "_latest_update"),
            ("vars[order]", "desc"), ("vars[sidebar]", "right"),
            ("vars[manga_archives_item_layout]", "big_thumbnail"),
        ]

    @staticmethod
    def _harem_search_load_more(
        page: int, query: str, filters: dict,
    ) -> list[tuple[str, str]]:
        data = [
            ("action", "madara_load_more"), ("page", str(page - 1)),
            ("template", "madara-core/content/content-search"),
            ("vars[paged]", "1"), ("vars[template]", "archive"),
            ("vars[sidebar]", "right"), ("vars[post_type]", "wp-manga"),
            ("vars[post_status]", "publish"),
            ("vars[manga_archives_item_layout]", "big_thumbnail"),
            ("vars[meta_query][0][key]", "_wp_manga_chapter_type"),
            ("vars[meta_query][0][value]", "manga"), ("vars[s]", query),
        ]
        tax_index, meta_index = 0, 1
        for key, taxonomy in (
            ("author", "wp-manga-author"), ("artist", "wp-manga-artist"),
            ("year", "wp-manga-release"),
        ):
            if str(filters.get(key, "")).strip():
                data.extend([
                    (f"vars[tax_query][{tax_index}][taxonomy]", taxonomy),
                    (f"vars[tax_query][{tax_index}][field]", "name"),
                    (f"vars[tax_query][{tax_index}][terms]", str(filters[key]).strip()),
                ])
                tax_index += 1
        statuses = filters.get("status", [])
        if isinstance(statuses, list) and statuses:
            data.append((f"vars[meta_query][{meta_index}][key]", "_wp_manga_status"))
            data.extend(
                (f"vars[meta_query][{meta_index}][value][{index}]", str(status))
                for index, status in enumerate(statuses)
            )
        order = str(filters.get("order", ""))
        data.extend({
            "latest": [("vars[orderby]", "meta_value_num"), ("vars[order]", "DESC"), ("vars[meta_key]", "_latest_update")],
            "alphabet": [("vars[orderby]", "post_title"), ("vars[order]", "ASC")],
            "rating": [("vars[orderby][query_average_reviews]", "DESC"), ("vars[orderby][query_total_reviews]", "DESC")],
            "trending": [("vars[orderby]", "meta_value_num"), ("vars[meta_key]", "_wp_manga_week_views_value"), ("vars[order]", "DESC")],
            "views": [("vars[orderby]", "meta_value_num"), ("vars[meta_key]", "_wp_manga_views"), ("vars[order]", "DESC")],
            "new-manga": [("vars[orderby]", "date"), ("vars[order]", "DESC")],
        }.get(order, []))
        adult = str(filters.get("adult", ""))
        if adult:
            data.extend([
                (f"vars[meta_query][{meta_index}][key]", "manga_adult_content"),
                (f"vars[meta_query][{meta_index}][value]", adult),
            ])
        return data
class GeneratedMadaraSource(HaremDeKiraSource):
    name = 'haremdekira_es'
    display_name = 'Harem de Kira'
    base_url = 'https://kiraproject.lat'
    language = 'es'
    manga_substring = 'serie'
    load_more = 'always'
    use_new_chapter_endpoint = False
    chapter_url_suffix = '?style=list'
    supports_latest = True
    requests_per_minute = 180
    pages_profile = 'default'
    extra_headers = {}
    image_headers = {}
    date_format = 'MMMM dd, yyyy'
    date_locale = 'es'
    details_profile = 'default'
    content_warning = 'mixed'

SOURCE = GeneratedMadaraSource
