"""Implementación común de sitios Blogger ZeistManga para Nyanko Source v3."""

import json
import re
from urllib.parse import unquote, urljoin

try:
    from .madara import (
        MadaraSource,
        SourceChapter,
        SourcePage,
        SourceSeries,
        _first,
        _image_url,
        _parse_html,
    )
except ImportError:
    pass


class ZeistMangaSource(MadaraSource):
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
    pages_profile = "default"
    latest_order = "published"
    strip_series_query = False

    def __init__(self, fetcher=None) -> None:
        super().__init__(fetcher)
        if self.request_referer:
            self.capabilities.headers["Referer"] = self.request_referer

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        if self.search_profile == "hanmokku":
            response = await self._request(
                "GET",
                f"{self.base_url}/search",
                params={"q": query.strip(), "max-results": 20},
            )
            response.raise_for_status()
            root = _parse_html(response.text)
            return [
                SourceSeries(
                    source_id=urljoin(str(response.url), anchor.attrs["href"]),
                    title=anchor.text().strip(),
                    source_name=self.name,
                )
                for anchor in root.descendants("a")
                if anchor.has_class("ck")
                and anchor.attrs.get("href")
                and anchor.text().strip()
            ][:limit]
        response = await self._feed(
            self.manga_category,
            params={"q": f"label:{self.manga_category} {query.strip()}", "max-results": 21},
        )
        return self._series_from_feed(response.json())[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind == "popular":
            if self.popular_is_latest:
                return await self.browse("latest", page)
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
        if kind != "latest":
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
            if self.manga_category not in categories or "Anime" in categories:
                continue
            link = next(
                (item.get("href") for item in entry.get("link") or [] if item.get("rel") == "alternate"),
                "",
            )
            title = (entry.get("title") or {}).get("$t", "")
            if link and title:
                result.append(SourceSeries(source_id=link, title=title, source_name=self.name))
        return result

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        response = await self._request("GET", series_id)
        response.raise_for_status()
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
            chapter_response = await self._feed(
                category,
                suffix=feed,
                params={"start-index": 1, "max-results": 999999},
            )
        result: list[SourceChapter] = []
        for entry in (chapter_response.json().get("feed") or {}).get("entry") or []:
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
                or self.pages_profile == "article_images"
                and self._has_ancestor_class(image, "post")
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
