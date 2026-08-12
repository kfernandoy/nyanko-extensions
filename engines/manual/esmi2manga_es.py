try:
    from .madara import MadaraSource, _Node, _TreeParser
except ImportError:
    pass

class MadaraSource:
    pass



class DragonTranslationOrgSource(MadaraSource):
    def __init__(self, fetcher=None) -> None:
        super().__init__(fetcher)
        self._genres: list[tuple[str, str]] | None = None
        self._genre_attempts = 0

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        return self._dragon_details(response)

    async def get_filters(self) -> list[SourceFilter]:
        if self._genres is None and self._genre_attempts < 3:
            self._genre_attempts += 1
            try:
                response = await self._request(
                    "GET", f"{self.base_url}/", params={"s": "genre", "post_type": "wp-manga"},
                )
                response.raise_for_status()
                root = _parse_html(response.text)
                group = _first(root, lambda node: node.tag == "div" and node.has_class("checkbox-group"))
                self._genres = [] if group is None else [
                    (control.attrs.get("value", ""), label.text().strip())
                    for box in group.descendants("div") if box.has_class("checkbox")
                    if (label := _first(box, lambda node: node.tag == "label")) is not None
                    and (control := _first(box, lambda node: node.tag == "input" and node.attrs.get("type") == "checkbox")) is not None
                ]
            except Exception:
                pass
        filters = [
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
        if self._genres:
            filters.extend([
                SourceFilter("genre_condition", "Condicion de generos", "select", [("", "O"), ("1", "Y")], ""),
                SourceFilter("genres", "Generos", "multi_select", self._genres, []),
            ])
        return filters

    async def browse(self, kind: str, page: int = 1):
        if kind not in {"popular", "latest"}:
            return {"items": [], "has_more": False}
        path = "manga/" if page == 1 else f"manga/page/{page}/"
        response = await self._request(
            "GET", urljoin(f"{self.base_url}/", path),
            params={"m_orderby": "views" if kind == "popular" else "latest"},
        )
        response.raise_for_status()
        return self._dragon_cards(response)

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
            response = await self._request("GET", f"{self.base_url}/manga/{query[5:]}/")
            response.raise_for_status()
            return {"items": [self._dragon_details(response)], "has_more": False}
        values = filters or {}
        path = "" if page == 1 else f"page/{page}/"
        params: list[tuple[str, str]] = [("s", query), ("post_type", "wp-manga")]
        for key, parameter in (("author", "author"), ("artist", "artist"), ("year", "release")):
            if str(values.get(key, "")).strip():
                params.append((parameter, str(values[key]).strip()))
        if isinstance(values.get("status"), list):
            params.extend(("status[]", str(status)) for status in values["status"])
        for key, parameter in (("order", "m_orderby"), ("adult", "adult"), ("genre_condition", "op")):
            if key == "adult" or values.get(key):
                params.append((parameter, str(values.get(key, ""))))
        if isinstance(values.get("genres"), list):
            params.extend(("genre[]", str(genre)) for genre in values["genres"])
        response = await self._request("GET", urljoin(f"{self.base_url}/", path), params=params)
        response.raise_for_status()
        return self._dragon_cards(response)

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        script = _first(
            _parse_html(response.text),
            lambda node: node.tag == "script" and node.attrs.get("id") == "mk-chapters-data",
        )
        if script is None:
            raise ValueError("DragonTranslation.org no publico mk-chapters-data")
        payload = json.loads("".join(child for child in script.children if isinstance(child, str)))
        result: list[SourceChapter] = []
        for item in payload.get("items", []):
            title = str(item.get("name", "")).strip()
            url = str(item.get("url", "")).strip()
            if not title or not url:
                continue
            number = re.search(r"\d+(?:\.\d+)?", title)
            result.append(SourceChapter(
                source_id=urljoin(str(response.url), url),
                title=title,
                series_id=series_id,
                source_name=self.name,
                number=float(number.group()) if number else None,
                language=self.language,
                uploaded_at=self._dragon_date(str(item.get("ago", ""))),
            ))
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        response = await self._request("GET", urljoin(f"{self.base_url}/", chapter_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        elements = [
            node for node in root.descendants()
            if (node.tag == "div" and node.has_class("page-break"))
            or (node.tag == "li" and node.has_class("blocks-gallery-item"))
            or (
                node.tag == "img" and (text_left := self._class_ancestor(node, "text-left")) is not None
                and self._has_class_ancestor(node, "reading-content")
                and not any(child.has_class("blocks-gallery-item") for child in text_left.descendants())
            )
        ]
        urls = []
        for element in elements:
            image = element if element.tag == "img" else _first(element, lambda node: node.tag == "img")
            if image is not None and (url := _image_url(image, str(response.url))):
                urls.append(url)
        return [SourcePage(
            source_id=url,
            chapter_id=chapter_id,
            index=index,
            filename=urlparse(url).path.rsplit("/", 1)[-1] or f"{index}.jpg",
            source_name=self.name,
        ) for index, url in enumerate(urls, 1)]

    def _dragon_cards(self, response) -> dict:
        root = _parse_html(response.text)
        items: list[SourceSeries] = []
        for card in root.descendants("a"):
            if (
                not card.has_class("acard") or card.parent is None
                or card.parent.tag != "div" or card.parent.attrs.get("id") != "mkAgrid"
            ):
                continue
            title = _first(card, lambda node: node.tag == "div" and node.has_class("ac-t"))
            if title is None or not card.attrs.get("href"):
                continue
            source_id = urljoin(str(response.url), card.attrs["href"])
            image = _first(card, lambda node: node.tag == "img")
            items.append(SourceSeries(
                source_id=source_id,
                title=self._own_text(title),
                source_name=self.name,
                cover_url=_image_url(image, str(response.url)) if image else None,
                web_url=source_id,
            ))
        has_more = any(
            node.tag == "a" and node.has_class("nextpostslink")
            and node.parent is not None and node.parent.tag == "div" and node.parent.has_class("wp-pagenavi")
            for node in root.descendants("a")
        )
        return {"items": items, "has_more": has_more}

    def _dragon_details(self, response) -> SourceSeries:
        root = _parse_html(response.text)
        hcol = _first(root, lambda node: node.tag == "div" and node.has_class("hcol"))
        poster = _first(root, lambda node: node.tag == "div" and node.has_class("hposter__card"))
        synopsis = _first(root, lambda node: node.tag == "div" and node.attrs.get("id") == "syn")
        if hcol is None:
            raise ValueError("DragonTranslation.org no publico los detalles")
        title = self._direct_child(hcol, lambda node: node.has_class("htitle"))
        tags = self._direct_child(hcol, lambda node: node.has_class("htags"))
        status_node = self._direct_child(tags, lambda node: node.has_class("htag--status")) if tags else None
        genres_box = self._direct_child(hcol, lambda node: node.has_class("hchips--genres"))
        status_text = status_node.text().strip().lower() if status_node else ""
        status = (
            "completed" if status_text in {"completed", "completo", "completado", "finalizado"}
            else "ongoing" if status_text in {"ongoing", "en curso", "emision", "publicandose", "publicandose"}
            else "hiatus" if status_text in {"on hold", "pausado", "en espera"}
            else "cancelled" if status_text in {"canceled", "cancelado"}
            else None
        )
        image = self._direct_child(poster, lambda node: node.tag == "img") if poster else None
        paragraphs = [child.text().strip() for child in synopsis.children if isinstance(child, _Node) and child.tag == "p"] if synopsis else []
        authors = [
            node.text().strip() for node in root.descendants("a")
            if node.parent is not None and node.parent.has_class("author-content") and node.text().strip()
        ]
        authors.extend(
            node.text().strip() for node in root.descendants("a")
            if node.parent is not None and node.parent.has_class("manga-authors") and node.text().strip()
        )
        artists = [
            node.text().strip() for node in root.descendants("a")
            if node.parent is not None and node.parent.has_class("artist-content") and node.text().strip()
        ]
        genres = tuple(
            child.text().strip() for child in genres_box.children
            if isinstance(child, _Node) and child.tag == "a" and child.has_class("chip") and child.text().strip()
        ) if genres_box else ()
        source_id = str(response.url)
        return SourceSeries(
            source_id=source_id,
            title=self._own_text(title) if title else "",
            source_name=self.name,
            cover_url=_image_url(image, str(response.url)) if image else None,
            description="\n\n".join(paragraphs) or None,
            author=", ".join(authors) or None,
            artist=", ".join(artists) or None,
            status=status,
            content_tags=genres,
            web_url=source_id,
        )

    @staticmethod
    def _direct_child(node, predicate):
        return next((child for child in node.children if isinstance(child, _Node) and predicate(child)), None)

    @staticmethod
    def _class_ancestor(node, class_name):
        parent = node.parent
        while parent is not None:
            if parent.has_class(class_name):
                return parent
            parent = parent.parent
        return None

    @staticmethod
    def _own_text(node) -> str:
        return " ".join(child.strip() for child in node.children if isinstance(child, str) and child.strip())

    @staticmethod
    def _dragon_date(value: str) -> str | None:
        from calendar import monthrange
        from datetime import datetime, timedelta
        months = {
            "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
            "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10,
            "noviembre": 11, "diciembre": 12,
        }
        text = value.strip().lower()
        absolute = re.fullmatch(r"([^\s]+)\s+(\d{1,2}),\s*(\d{4})", text)
        if absolute and absolute.group(1) in months:
            return datetime(int(absolute.group(3)), months[absolute.group(1)], int(absolute.group(2))).isoformat()
        relative = re.search(r"(\d+)", text)
        if not text.startswith("hace") or relative is None:
            return None
        amount, now = int(relative.group()), datetime.now().replace(microsecond=0)
        if "dia" in text or "día" in text:
            result = now - timedelta(days=amount)
        elif "hora" in text:
            result = now - timedelta(hours=amount)
        elif "minuto" in text or " min" in text:
            result = now - timedelta(minutes=amount)
        elif "segundo" in text:
            result = now - timedelta(seconds=amount)
        elif "semana" in text:
            result = now - timedelta(days=amount * 7)
        elif "mes" in text:
            total = now.year * 12 + now.month - 1 - amount
            year, month = divmod(total, 12)
            result = now.replace(year=year, month=month + 1, day=min(now.day, monthrange(year, month + 1)[1]))
        elif "ano" in text or "año" in text:
            year = now.year - amount
            result = now.replace(year=year, day=min(now.day, monthrange(year, now.month)[1]))
        else:
            return None
        return result.isoformat()


class EsMi2MangaSource(DragonTranslationOrgSource):
    def __init__(self, fetcher=None) -> None:
        super().__init__(fetcher)
        self._load_more_detected = False

    async def browse(self, kind: str, page: int = 1):
        if kind not in {"popular", "latest"}:
            return {"items": [], "has_more": False}
        if self._load_more_detected:
            response = await self._request(
                "POST", f"{self.base_url}/wp-admin/admin-ajax.php",
                data=self._esmi_load_more(page, kind == "popular"),
            )
        else:
            path = "manga/" if page == 1 else f"manga/page/{page}/"
            response = await self._request(
                "GET", urljoin(f"{self.base_url}/", path),
                params={"m_orderby": "views" if kind == "popular" else "latest"},
            )
        response.raise_for_status()
        return self._esmi_page(response, search=False)

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
            response = await self._request("GET", f"{self.base_url}/manga/{query[5:]}/")
            response.raise_for_status()
            return {"items": [self._esmi_details(response)], "has_more": False}
        values = filters or {}
        if self._load_more_detected:
            response = await self._request(
                "POST", f"{self.base_url}/wp-admin/admin-ajax.php",
                data=self._esmi_search_load_more(page, query, values),
            )
        else:
            path = "" if page == 1 else f"page/{page}/"
            params: list[tuple[str, str]] = [("s", query), ("post_type", "wp-manga")]
            for key, parameter in (("author", "author"), ("artist", "artist"), ("year", "release")):
                if str(values.get(key, "")).strip():
                    params.append((parameter, str(values[key]).strip()))
            if isinstance(values.get("status"), list):
                params.extend(("status[]", str(status)) for status in values["status"])
            for key, parameter in (("order", "m_orderby"), ("adult", "adult"), ("genre_condition", "op")):
                if key == "adult" or values.get(key):
                    params.append((parameter, str(values.get(key, ""))))
            if isinstance(values.get("genres"), list):
                params.extend(("genre[]", str(genre)) for genre in values["genres"])
            response = await self._request("GET", urljoin(f"{self.base_url}/", path), params=params)
        response.raise_for_status()
        return self._esmi_page(response, search=True)

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        series_url = urljoin(f"{self.base_url}/", series_id)
        response = await self._request("GET", series_url)
        response.raise_for_status()
        root = _parse_html(response.text)
        items = [node for node in root.descendants("li") if node.has_class("wp-manga-chapter")]
        holder = _first(root, lambda node: node.attrs.get("id", "").startswith("manga-chapters-holder"))
        if not items and holder is not None:
            chapter_response = await self._request(
                "POST", f"{self.base_url}/wp-admin/admin-ajax.php",
                data={"action": "manga_get_chapters", "manga": holder.attrs.get("data-id", "")},
            )
            if getattr(chapter_response, "status_code", 200) == 400:
                chapter_response = await self._request("POST", f"{series_url.rstrip('/')}/ajax/chapters")
            chapter_response.raise_for_status()
            items = [
                node for node in _parse_html(chapter_response.text).descendants("li")
                if node.has_class("wp-manga-chapter")
            ]
        result: list[SourceChapter] = []
        for item in items:
            anchor = _first(item, lambda node: node.tag == "a" and bool(node.attrs.get("href")))
            if anchor is None:
                continue
            title = anchor.text().strip()
            relative_image = _first(item, lambda node: node.tag == "img" and not node.has_class("thumb"))
            relative_link = _first(
                item,
                lambda node: node.tag == "a" and node.parent is not None
                and node.parent.tag == "span" and bool(node.attrs.get("title")),
            )
            date = _first(item, lambda node: node.tag == "span" and node.has_class("chapter-release-date"))
            date_text = (
                relative_image.attrs.get("alt", "") if relative_image is not None
                else relative_link.attrs.get("title", "") if relative_link is not None
                else date.text() if date else ""
            )
            chapter_url = urljoin(series_url, anchor.attrs["href"]).split("?style=paged", 1)[0]
            if not chapter_url.endswith(self.chapter_url_suffix):
                chapter_url += self.chapter_url_suffix
            number = re.search(r"\d+(?:\.\d+)?", title)
            result.append(SourceChapter(
                source_id=chapter_url,
                title=title or "Capítulo",
                series_id=series_id,
                source_name=self.name,
                number=float(number.group()) if number else None,
                language=self.language,
                uploaded_at=self._dragon_date(date_text),
            ))
        return result

    def _esmi_page(self, response, search: bool) -> dict:
        root = _parse_html(response.text)
        result: list[SourceSeries] = []
        for item in root.descendants("div"):
            if not self._has_class_ancestor(item, "site-content"):
                continue
            if search:
                if not item.has_class("c-tabs-item__content"):
                    continue
            elif (
                not item.has_class("page-item-detail") or not item.has_class("manga")
                or any("bilibilicomics.com" in node.attrs.get("href", "") for node in item.descendants("a"))
            ):
                continue
            title_box = _first(item, lambda node: node.has_class("post-title"))
            anchor = _first(title_box or item, lambda node: node.tag == "a" and bool(node.attrs.get("href")))
            if anchor is None:
                continue
            source_id = urljoin(str(response.url), anchor.attrs["href"])
            image = _first(item, lambda node: node.tag == "img")
            result.append(SourceSeries(
                source_id=source_id,
                title=self._own_text(anchor),
                source_name=self.name,
                cover_url=_image_url(image, str(response.url)) if image else None,
                web_url=source_id,
            ))
        root_has_no_posts = any(node.has_class("no-posts") for node in root.descendants())
        has_more = (
            not root_has_no_posts if self._load_more_detected
            else any(
                node.has_class("nav-previous") or node.has_class("navigation-ajax")
                or (node.tag == "a" and node.has_class("nextpostslink"))
                for node in root.descendants()
            )
        )
        if any(node.tag == "nav" and node.has_class("navigation-ajax") for node in root.descendants()):
            self._load_more_detected = True
        return {"items": result, "has_more": has_more}

    @staticmethod
    def _esmi_load_more(page: int, popular: bool) -> list[tuple[str, str]]:
        return [
            ("action", "madara_load_more"), ("page", str(page - 1)),
            ("template", "madara-core/content/content-archive"), ("vars[orderby]", "meta_value_num"),
            ("vars[paged]", "1"), ("vars[meta_query][0][key]", "_wp_manga_chapter_type"),
            ("vars[meta_query][0][value]", "manga"), ("vars[post_type]", "wp-manga"),
            ("vars[post_status]", "publish"),
            ("vars[meta_key]", "_wp_manga_views" if popular else "_latest_update"),
            ("vars[order]", "desc"), ("vars[sidebar]", "right"),
            ("vars[manga_archives_item_layout]", "big_thumbnail"),
        ]

    @staticmethod
    def _esmi_search_load_more(page: int, query: str, filters: dict) -> list[tuple[str, str]]:
        data: list[tuple[str, str]] = [
            ("action", "madara_load_more"), ("page", str(page - 1)),
            ("template", "madara-core/content/content-search"), ("vars[paged]", "1"),
            ("vars[template]", "archive"), ("vars[sidebar]", "right"),
            ("vars[post_type]", "wp-manga"), ("vars[post_status]", "publish"),
            ("vars[manga_archives_item_layout]", "big_thumbnail"),
            ("vars[meta_query][0][key]", "_wp_manga_chapter_type"),
            ("vars[meta_query][0][value]", "manga"), ("vars[s]", query),
        ]
        tax_index, meta_index = 0, 1
        for key, taxonomy in (("author", "wp-manga-author"), ("artist", "wp-manga-artist"), ("year", "wp-manga-release")):
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
            data.extend((f"vars[meta_query][{meta_index}][value][{index}]", str(status)) for index, status in enumerate(statuses))
            meta_index += 1
        order = str(filters.get("order", ""))
        order_values = {
            "latest": [("vars[orderby]", "meta_value_num"), ("vars[order]", "DESC"), ("vars[meta_key]", "_latest_update")],
            "alphabet": [("vars[orderby]", "post_title"), ("vars[order]", "ASC")],
            "rating": [("vars[orderby][query_average_reviews]", "DESC"), ("vars[orderby][query_total_reviews]", "DESC")],
            "trending": [("vars[orderby]", "meta_value_num"), ("vars[meta_key]", "_wp_manga_week_views_value"), ("vars[order]", "DESC")],
            "views": [("vars[orderby]", "meta_value_num"), ("vars[meta_key]", "_wp_manga_views"), ("vars[order]", "DESC")],
            "new-manga": [("vars[orderby]", "date"), ("vars[order]", "DESC")],
        }
        data.extend(order_values.get(order, []))
        adult = str(filters.get("adult", ""))
        if adult:
            data.extend([
                (f"vars[meta_query][{meta_index}][key]", "manga_adult_content"),
                (f"vars[meta_query][{meta_index}][compare]", "not exists" if adult == "0" else "exists"),
            ])
            meta_index += 1
        genres = filters.get("genres", [])
        if isinstance(genres, list) and genres:
            if filters.get("genre_condition") == "1":
                data.append((f"vars[tax_query][{tax_index}][operation]", "AND"))
            data.extend([
                (f"vars[tax_query][{tax_index}][taxonomy]", "wp-manga-genre"),
                (f"vars[tax_query][{tax_index}][field]", "slug"),
            ])
            data.extend((f"vars[tax_query][{tax_index}][terms][{index}]", str(genre)) for index, genre in enumerate(genres))
        return data

    def _esmi_details(self, response) -> SourceSeries:
        root = _parse_html(response.text)
        title = _first(root, lambda node: node.tag in {"h1", "h3"} and self._has_class_ancestor(node, "post-title"))
        image_box = _first(root, lambda node: node.has_class("summary_image"))
        image = _first(image_box, lambda node: node.tag == "img") if image_box else None
        description_box = _first(root, lambda node: node.has_class("summary__content") and self._has_class_ancestor(node, "description-summary"))
        authors = [node.text().strip() for node in root.descendants("a") if node.parent and node.parent.has_class("author-content")]
        artists = [node.text().strip() for node in root.descendants("a") if node.parent and node.parent.has_class("artist-content")]
        genres_box = _first(root, lambda node: node.has_class("genres-content"))
        genres = tuple(node.text().strip() for node in genres_box.descendants("a")) if genres_box else ()
        statuses = [node for node in root.descendants("div") if node.has_class("summary-content")]
        status_text = statuses[-1].text().strip().lower() if statuses else ""
        status = (
            "completed" if status_text in {"completed", "completo", "completado", "finalizado"}
            else "ongoing" if status_text in {"ongoing", "en curso", "emision", "publicandose", "publicándose"}
            else "hiatus" if status_text in {"on hold", "pausado", "en espera"}
            else "cancelled" if status_text in {"canceled", "cancelado"}
            else None
        )
        source_id = str(response.url)
        return SourceSeries(
            source_id=source_id,
            title=title.text().strip() if title else "",
            source_name=self.name,
            cover_url=_image_url(image, source_id) if image else None,
            description=description_box.text().strip() if description_box else None,
            author=", ".join(authors) or None,
            artist=", ".join(artists) or None,
            status=status,
            content_tags=genres,
            web_url=source_id,
        )
class GeneratedMadaraSource(EsMi2MangaSource):
    name = 'esmi2manga_es'
    display_name = 'Es.Mi2Manga'
    base_url = 'https://es.mi2manga.com'
    language = 'es'
    manga_substring = 'manga'
    load_more = 'auto'
    use_new_chapter_endpoint = False
    chapter_url_suffix = '?style=list'
    supports_latest = True
    requests_per_minute = 120
    pages_profile = 'default'
    extra_headers = {}
    image_headers = {}
    date_format = 'MMMM dd, yyyy'
    date_locale = 'es'
    details_profile = 'default'
    content_warning = 'nsfw'

SOURCE = GeneratedMadaraSource
