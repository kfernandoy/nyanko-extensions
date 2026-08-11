"""Implementación común de sitios Blogger ZeistManga para Nyanko Source v4."""

import json
import re
from urllib.parse import quote, unquote, urljoin

try:
    from .base import (
        FuenteBaseSource,
        SourceChapter,
        SourceFilter,
        SourcePage,
        SourceSeries,
        _first,
        _image_url,
        _parse_html,
    )
except ImportError:
    pass


class ZeistMangaSource(FuenteBaseSource):
    manga_category = "Series"
    chapter_category = "Chapter"
    use_new_chapter_feed = False
    chapter_feed_profile = "default"
    popular_is_latest = False
    popular_profile = "default"
    request_referer = ""
    search_profile = "default"
    chapter_profile = "default"
    chapter_categories: tuple[str, ...] = ()
    use_old_chapter_feed = False
    paginate_chapter_feed = False
    pages_profile = "default"
    latest_order = "published"
    strip_series_query = False
    has_filters = False
    has_language_filter = True
    excluded_categories = ("Anime",)
    status_filters: tuple[tuple[str, str], ...] = ()
    type_filters: tuple[tuple[str, str], ...] = ()
    genre_filters: tuple[tuple[str, str], ...] = ()
    details_profile = "default"

    def __init__(self, fetcher=None) -> None:
        super().__init__(fetcher)
        if self.request_referer:
            self.capabilities.headers["Referer"] = self.request_referer

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        if self.search_profile == "hanmokku":
            response = await self._request(
                "GET",
                f"{self.base_url}/search",
                params={"q": query.strip(), "max-results": 20},
            )
            response.raise_for_status()
            root = _parse_html(response.text)
            items = [
                SourceSeries(
                    source_id=urljoin(str(response.url), anchor.attrs["href"]),
                    title=anchor.text().strip(),
                    source_name=self.name,
                )
                for anchor in root.descendants("a")
                if anchor.has_class("ck")
                and anchor.attrs.get("href")
                and anchor.text().strip()
            ][:20]
            return {"items": items, "has_more": False}
        params = {"max-results": 21, "start-index": 20 * (page - 1) + 1}
        category = self.manga_category
        if query.strip():
            params["q"] = f"label:{self.manga_category} {query.strip()}"
        elif self.has_filters:
            values = filters or {}
            selected = [
                values.get("status", self.status_filters[0][0] if self.status_filters else ""),
                values.get("type", self.type_filters[0][0] if self.type_filters else ""),
            ]
            if self.has_language_filter:
                selected.append(values.get("language", ""))
            genres = values.get("genres", [])
            if isinstance(genres, list):
                selected.extend(genres)
            suffix = "/".join(quote(str(value), safe="") for value in selected if value)
            if suffix:
                category += f"/{suffix}"
        response = await self._feed(
            category,
            params=params,
        )
        items = self._series_from_feed(response.json())
        return {"items": items[:20], "has_more": len(items) == 21}

    def get_filters(self) -> list[SourceFilter]:
        if not self.has_filters:
            return []
        statuses = list(self.status_filters) or [
            ("", "Todos"), ("Ongoing", "En curso"), ("Completed", "Completado"),
            ("Dropped", "Abandonado"), ("Upcoming", "Proximos"),
            ("Hiatus", "En hiatus"), ("Cancelled", "Cancelado"),
        ]
        types = list(self.type_filters) or [
            ("", "Todos"), ("Manga", "Manga"), ("Manhua", "Manhua"),
            ("Manhwa", "Manhwa"), ("Novel", "Novela"),
            ("Web Novel (JP)", "Web Novel (JP)"), ("Web Novel (KR)", "Web Novel (KR)"),
            ("Web Novel (CN)", "Web Novel (CN)"), ("Doujinshi", "Doujinshi"),
        ]
        filters = [
            SourceFilter("status", "Estado", "select", statuses, statuses[0][0]),
            SourceFilter("type", "Tipo", "select", types, types[0][0]),
        ]
        if self.has_language_filter:
            filters.append(SourceFilter("language", "Idioma", "select", [
                ("", "Todos"), ("Indonesian", "Indonesian"), ("English", "English"),
            ], ""))
        genres = list(self.genre_filters) or [
            (value, value) for value in (
                "Action", "Adventurer", "Comedy", "Dementia", "Drama", "Ecchi", "Fantasy",
                "Game", "Harem", "Historical", "Horror", "Josei", "Magic", "Martial Arts",
                "Mecha", "Military", "Music", "Mystery", "Parody", "Police", "Psychological",
                "Romance", "Samurai", "School", "Sci-fi", "Seinen", "Shoujo", "Shoujo Ai",
                "Shounen", "Slice of Life", "Space", "Sports", "Super Power", "SuperNatural",
                "Thriller", "Vampire", "Work Life", "Yuri",
            )
        ]
        filters.append(SourceFilter("genres", "Genero", "multi_select", genres, []))
        return filters

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind == "popular" and not self.popular_is_latest:
            if self.popular_profile == "serieslist" and page > 1:
                response = await self._feed(
                    self.manga_category,
                    params={"max-results": 21, "start-index": 20 * (page - 1) + 1},
                )
                return self._series_from_feed(response.json())[:20]
            if page != 1:
                return []
            response = await self._request("GET", self.base_url)
            response.raise_for_status()
            return self._popular(response.text, str(response.url))
        if kind == "latest" and not self.supports_latest:
            return []
        if kind not in {"popular", "latest"}:
            return []
        response = await self._feed(
            self.manga_category,
            params={
                "orderby": self.latest_order,
                "max-results": 21,
                "start-index": 20 * (page - 1) + 1,
            },
        )
        return self._series_from_feed(response.json())[:20]

    def _popular(self, html: str, response_url: str) -> list[SourceSeries]:
        root = _parse_html(html)
        if self.popular_profile == "gistamis":
            result: list[SourceSeries] = []
            for figure in root.descendants("figure"):
                if not (
                    self._has_ancestor_class(figure, "PopularPosts")
                    and self._has_ancestor_class(figure, "grid")
                    and not any(
                        node.tag == "span" and node.attrs.get("data") == "Capitulo"
                        for node in figure.descendants()
                    )
                ):
                    continue
                anchor = _first(
                    figure,
                    lambda node: node.tag == "a"
                    and bool(node.attrs.get("href"))
                    and bool(node.text().strip()),
                )
                if anchor is None:
                    continue
                image = _first(figure, lambda node: node.tag == "img")
                result.append(SourceSeries(
                    source_id=urljoin(response_url, anchor.attrs["href"]),
                    title=anchor.text().strip(),
                    source_name=self.name,
                    cover_url=_image_url(image, response_url) if image else None,
                    web_url=urljoin(response_url, anchor.attrs["href"]),
                ))
            return result
        if self.popular_profile != "default":
            containers = [
                node
                for node in root.descendants()
                if (
                    self.popular_profile == "pop_card"
                    and node.tag == "div"
                    and node.has_class("pop-card")
                    or self.popular_profile == "serieslist"
                    and node.tag == "li"
                    and self._has_ancestor_class(node, "serieslist")
                    or self.popular_profile == "gallery"
                    and node.tag == "li"
                    and node.has_class("bg")
                    and self._has_ancestor_class(node, "gallery")
                )
            ]
            result: list[SourceSeries] = []
            for container in containers:
                anchor = _first(
                    container,
                    lambda item: item.tag == "a"
                    and bool(item.attrs.get("href"))
                    and bool(item.text().strip()),
                )
                if anchor is None:
                    continue
                result.append(
                    SourceSeries(
                        source_id=urljoin(response_url, anchor.attrs["href"]),
                        title=anchor.text().strip() or "Manga",
                        source_name=self.name,
                    )
                )
            return result
        result: list[SourceSeries] = []
        seen: set[str] = set()
        for node in root.descendants():
            if not (
                self._has_ancestor_class(node, "PopularPosts")
                or self._has_ancestor_id_contains(node, "PopularPosts")
            ):
                continue
            anchor = node if node.tag == "a" and node.attrs.get("href") else None
            if anchor is not None and anchor.text().strip() and anchor.attrs["href"] not in seen:
                href = anchor.attrs["href"].split("?", 1)[0] if self.strip_series_query else anchor.attrs["href"]
                seen.add(href)
                result.append(
                    SourceSeries(
                        source_id=urljoin(response_url, href),
                        title=anchor.text().strip(),
                        source_name=self.name,
                    )
                )
        return result

    def _series_from_feed(self, payload: dict) -> list[SourceSeries]:
        result: list[SourceSeries] = []
        for entry in (payload.get("feed") or {}).get("entry") or []:
            categories = {item.get("term") for item in entry.get("category") or []}
            if self.manga_category not in categories or categories & set(self.excluded_categories):
                continue
            link = next(
                (item.get("href") for item in entry.get("link") or [] if item.get("rel") == "alternate"),
                "",
            )
            title = (entry.get("title") or {}).get("$t", "")
            if link and title:
                thumbnail = (entry.get("media$thumbnail") or {}).get("url", "")
                if thumbnail:
                    thumbnail = re.sub(r"/s.+?-c/", "/w600/", thumbnail)
                    thumbnail = re.sub(r"=s(?!.*=s).+?-c$", "=w600", thumbnail)
                else:
                    content = (entry.get("content") or {}).get("$t", "")
                    image = _first(_parse_html(content), lambda node: node.tag == "img")
                    thumbnail = _image_url(image, self.base_url) if image else ""
                result.append(SourceSeries(
                    source_id=link,
                    title=title,
                    source_name=self.name,
                    cover_url=thumbnail or None,
                    web_url=link,
                ))
        return result

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        if self.details_profile != "gistamis":
            return series if isinstance(series, SourceSeries) else SourceSeries(
                source_id=series_id, title=series_id.rstrip("/").rsplit("/", 1)[-1], source_name=self.name,
            )
        response = await self._request("GET", series_id)
        response.raise_for_status()
        root = _parse_html(response.text)
        profile = _first(root, lambda node: node.has_class("grid") and node.has_class("gtc-235fr"))
        if profile is None:
            raise ValueError("No se encontró la ficha del manga")
        image = _first(profile, lambda node: node.tag == "img")
        synopsis = _first(profile, lambda node: node.attrs.get("id") == "synopsis")
        alt_holder = _first(
            profile,
            lambda node: node.tag == "div" and node.has_class("y6x11p")
            and "Otros Nombres" in node.text(),
        )
        alt_name = _first(alt_holder, lambda node: node.tag == "span" and node.has_class("dt")) if alt_holder else None
        description = synopsis.text().strip() if synopsis else ""
        if alt_name and alt_name.text().strip():
            description = f"{description}\n\nOtros Nombres: {alt_name.text().strip()}".strip()
        genres = tuple(
            node.text().strip()
            for node in profile.descendants("a")
            if node.attrs.get("rel") == "tag"
            and self._has_ancestor_class(node, "mt-15")
            and node.text().strip()
        )
        author = artist = status = None
        status_found = False
        for info in (node for node in profile.descendants() if node.has_class("y6x11p")):
            label = " ".join(child.strip() for child in info.children if isinstance(child, str) and child.strip())
            value = " ".join(
                node.text().strip() for node in info.descendants("span")
                if node.has_class("dt") and node.text().strip()
            )
            if not value:
                continue
            if any(name in label for name in ("Status", "Estado", "الحالة")):
                if not status_found:
                    status = self._zeist_status(value)
                status_found = True
            elif any(name in label for name in ("Author", "Autor", "Mangaka")):
                author = value
            elif any(name in label for name in ("Artist", "Artista", "الرسام", "Çizer")):
                artist = value
        title = series.title if isinstance(series, SourceSeries) else series_id.rstrip("/").rsplit("/", 1)[-1]
        return SourceSeries(
            source_id=series_id,
            title=title,
            source_name=self.name,
            cover_url=_image_url(image, str(response.url)) if image else None,
            description=description or None,
            author=author,
            artist=artist,
            status=status,
            content_tags=genres,
            metadata=series.metadata if isinstance(series, SourceSeries) else {},
            web_url=str(response.url),
        )

    @staticmethod
    def _zeist_status(value: str) -> str | None:
        normalized = value.casefold().strip()
        if normalized in {"ongoing", "en curso", "en emisión", "activo", "ativo", "lançando", "مستمر", "مستمرة"}:
            return "ongoing"
        if normalized in {"completed", "completo", "finalizado", "مكتمل", "مكتملة"}:
            return "completed"
        if normalized in {"hiatus", "pausado"}:
            return "hiatus"
        if normalized in {"cancelled", "dropped", "dropado", "abandonado", "cancelado"}:
            return "cancelled"
        return None

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        response = await self._request("GET", series_id)
        response.raise_for_status()
        chapter_entries = None
        if self.chapter_profile == "html_list":
            return self._html_chapters(response.text, series_id, str(response.url))
        if self.use_old_chapter_feed:
            root = _parse_html(response.text)
            script = next(
                (
                    node
                    for node in root.descendants("script")
                    if node.attrs.get("src") and self._has_ancestor_id_contains(node, "myUL")
                ),
                None,
            )
            if script is None:
                raise ValueError("No se encontró el feed antiguo de capítulos")
            chapter_response = await self._request(
                "GET",
                urljoin(self.base_url, script.attrs["src"].split("?", 1)[0]),
                params={"alt": "json"},
            )
            chapter_response.raise_for_status()
        else:
            category, feed = self._chapter_feed(response.text)
            if self.paginate_chapter_feed:
                probe = await self._feed(category, suffix=feed, params={"start-index": 1, "max-results": 0})
                probe_payload = probe.json()
                total_value = (
                    probe_payload.get("openSearch$totalResults")
                    or (probe_payload.get("feed") or {}).get("openSearch$totalResults")
                    or {"$t": "150"}
                )
                total = int(total_value.get("$t", 150))
                chapter_entries = []
                start = 1
                while len(chapter_entries) < total:
                    batch = await self._feed(category, suffix=feed, params={"start-index": start, "max-results": 150})
                    entries = (batch.json().get("feed") or {}).get("entry") or []
                    if not entries:
                        break
                    chapter_entries.extend(entries)
                    start += len(entries)
            else:
                chapter_response = await self._feed(
                    category,
                    suffix=feed,
                    params={"start-index": 1, "max-results": 999999},
                )
        result: list[SourceChapter] = []
        entries = chapter_entries if chapter_entries is not None else (chapter_response.json().get("feed") or {}).get("entry") or []
        for entry in entries:
            categories = {item.get("term") for item in entry.get("category") or []}
            expected = set(self.chapter_categories or (self.chapter_category,))
            if not categories & expected:
                continue
            link = next(
                (item.get("href") for item in entry.get("link") or [] if item.get("rel") == "alternate"),
                "",
            )
            title = (entry.get("title") or {}).get("$t", "")
            match = re.search(r"(\d+(?:\.\d+)?)", title)
            if self.chapter_profile == "yokai" and title.lower().startswith("chapter"):
                title = f"الفصل {title[7:].strip()}"
            result.append(
                SourceChapter(
                    source_id=link,
                    title=title or "Capítulo",
                    series_id=series_id,
                    source_name=self.name,
                    number=float(match.group(1)) if match else None,
                    uploaded_at=(entry.get("published") or entry.get("updated") or {}).get("$t"),
                )
            )
        if self.chapter_profile == "number_desc":
            result.sort(key=lambda chapter: chapter.number or -1, reverse=True)
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        response = await self._request("GET", chapter_id)
        response.raise_for_status()
        root = _parse_html(response.text)
        if self.pages_profile == "textarea_raw":
            textarea = next(
                (node for node in root.descendants("textarea") if node.attrs.get("id") == "zeist-raw-data"),
                None,
            )
            root = _parse_html(textarea.text() if textarea else "")
        elif self.pages_profile == "template_html":
            match = re.search(r"const\s+content\s*=\s*`(.*?)`;", response.text, re.S)
            root = _parse_html(match.group(1) if match else "")
        elif self.pages_profile == "json_array":
            match = re.search(r"=\s*(\[[^\]]+\])", response.text, re.S)
            urls = json.loads(match.group(1)) if match else []
            return self._source_pages(urls, chapter_id)
        elif self.pages_profile == "ulas_script":
            script = response.text.partition("config['chapterImage']")[2]
            urls = re.findall(r'"(https?://[^"]+)"', script)
            if urls:
                return self._source_pages(urls, chapter_id)
        elif self.pages_profile == "reader_separators":
            urls = [
                _image_url(image, str(response.url))
                for image in root.descendants("img")
                if self._has_ancestor_class(image, "separator")
                and self._has_ancestor_id_contains(image, "reader")
            ]
            return self._source_pages(urls, chapter_id)
        elif self.pages_profile == "check_box_separators":
            urls = [
                _image_url(image, str(response.url))
                for image in root.descendants("img")
                if self._has_ancestor_class(image, "separator")
                and self._has_ancestor_class(image, "check-box")
            ]
            return self._source_pages(urls, chapter_id)
        elif self.pages_profile == "article_images":
            urls = [
                _image_url(image, str(response.url))
                for image in root.descendants("img")
                if self._has_ancestor_tag(image, "p")
                and self._has_ancestor_class(image, "post")
                and self._has_ancestor_tag_class(image, "article", "oh")
            ]
            return self._source_pages(urls, chapter_id)
        if self.pages_profile == "separator_links":
            urls = [
                urljoin(str(response.url), node.attrs["href"])
                for node in root.descendants("a")
                if node.attrs.get("href") and self._has_ancestor_class(node, "separator")
            ]
            return self._source_pages(urls, chapter_id)
        urls = [
            _image_url(image, str(response.url))
            for image in root.descendants("img")
            if (
                self.pages_profile == "broad_separators"
                and self._has_ancestor_class(image, "separator")
                or self._has_ancestor_class(image, "separator")
                and self._has_ancestor_class(image, "check-box")
                or self._has_ancestor_id_contains(image, "reader")
            )
        ]
        return self._source_pages(urls, chapter_id)

    def _source_pages(self, urls: list[str], chapter_id: str) -> list[SourcePage]:
        return [
            SourcePage(
                source_id=url,
                chapter_id=chapter_id,
                index=index,
                filename=url.rsplit("/", 1)[-1].split("?", 1)[0] or f"{index}.jpg",
                source_name=self.name,
            )
            for index, url in enumerate(dict.fromkeys(url for url in urls if url), 1)
        ]

    def _html_chapters(self, html: str, series_id: str, response_url: str) -> list[SourceChapter]:
        root = _parse_html(html)
        result: list[SourceChapter] = []
        for node in root.descendants("div"):
            if not node.has_class("flexch-infoz") or not self._has_ancestor_class(node, "series-chapterlist"):
                continue
            anchor = _first(node, lambda item: item.tag == "a" and bool(item.attrs.get("href")))
            if anchor is None:
                continue
            title_node = _first(node, lambda item: item.tag == "span" and bool(item.text().strip()))
            title = title_node.text().strip() if title_node else anchor.text().strip() or "Capítulo"
            match = re.search(r"(\d+(?:\.\d+)?)", title)
            result.append(
                SourceChapter(
                    source_id=urljoin(response_url, anchor.attrs["href"]),
                    title=title,
                    series_id=series_id,
                    source_name=self.name,
                    number=float(match.group(1)) if match else None,
                )
            )
        return result

    async def _feed(self, category: str, *, suffix: str = "", params: dict | None = None):
        path = f"{self.base_url}/feeds/posts/default/-/{category}"
        if suffix:
            path += f"/{suffix.strip('/')}"
        response = await self._request("GET", path, params={"alt": "json", **(params or {})})
        response.raise_for_status()
        return response

    def _chapter_feed(self, html: str) -> tuple[str, str]:
        if self.chapter_feed_profile == "comicverse":
            root = _parse_html(html)
            label = next(
                (
                    node.attrs["data-label"]
                    for node in root.descendants("div")
                    if node.has_class("manga-widget") and node.attrs.get("data-label")
                ),
                "",
            )
            if not label:
                raise ValueError("No se encontró el feed de capítulos")
            return self.chapter_category, label
        if self.chapter_feed_profile in {"data_label", "og_title", "title", "cat_name"}:
            root = _parse_html(html)
            if self.chapter_feed_profile == "data_label":
                node = next(
                    (
                        item
                        for item in root.descendants()
                        if item.has_class("chapter_get") and item.attrs.get("data-labelchapter")
                    ),
                    None,
                )
                return (node.attrs["data-labelchapter"], "") if node else self._missing_feed()
            if self.chapter_feed_profile == "og_title":
                node = next(
                    (
                        item
                        for item in root.descendants("meta")
                        if item.attrs.get("property") == "og:title" and item.attrs.get("content")
                    ),
                    None,
                )
                if node:
                    return self.chapter_category, node.attrs["content"]
            if self.chapter_feed_profile == "title":
                node = next(
                    (item for item in root.descendants("h1") if item.has_class("entry-title")),
                    None,
                )
                return (node.text().strip(), "") if node else self._missing_feed()
            if self.chapter_feed_profile == "cat_name":
                match = re.search(r"catNameProject.*?=\s+?\('([^']+)", html, re.S)
                return (self.chapter_category, match.group(1)) if match else self._missing_feed()

        match = None if self.use_new_chapter_feed else re.search(
            r"""clwd\.run\(["'](.*?)["']\)""",
            html,
        )
        category, suffix = (
            (self.chapter_category, match.group(1))
            if match
            else (self._new_feed(html), "")
        )
        if self.chapter_feed_profile == "yurimoon":
            category = re.sub(r"\s{2,}", "", re.sub(r"[\u0600-\u06ff]", "", unquote(category)))
            suffix = re.sub(r"\s{2,}", "", re.sub(r"[\u0600-\u06ff]", "", unquote(suffix)))
        return category, suffix

    @staticmethod
    def _missing_feed():
        raise ValueError("No se encontró el feed de capítulos")

    @staticmethod
    def _new_feed(html: str) -> str:
        match = re.search(r"""label\s*=\s*'([^']+)'""", html)
        if match is None:
            raise ValueError("No se encontró el feed de capítulos")
        return match.group(1)

    @staticmethod
    def _has_ancestor_class(node: object, class_name: str) -> bool:
        parent = getattr(node, "parent", None)
        while parent is not None:
            if parent.has_class(class_name):
                return True
            parent = parent.parent
        return False

    @staticmethod
    def _has_ancestor_id_contains(node: object, value: str) -> bool:
        parent = getattr(node, "parent", None)
        while parent is not None:
            if value.lower() in parent.attrs.get("id", "").lower():
                return True
            parent = parent.parent
        return False

    @staticmethod
    def _has_ancestor_tag(node: object, tag: str) -> bool:
        parent = getattr(node, "parent", None)
        while parent is not None:
            if parent.tag == tag:
                return True
            parent = parent.parent
        return False

    @staticmethod
    def _has_ancestor_tag_class(node: object, tag: str, class_name: str) -> bool:
        parent = getattr(node, "parent", None)
        while parent is not None:
            if parent.tag == tag and parent.has_class(class_name):
                return True
            parent = parent.parent
        return False
