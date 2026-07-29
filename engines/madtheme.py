"""Implementación común de sitios MadTheme para Nyanko Source v3."""

import re
from urllib.parse import urljoin, urlsplit, urlunsplit

try:
    from .madara import (
        MadaraSource,
        SourceChapter,
        SourcePage,
        SourcePageContent,
        SourceSeries,
        _first,
        _image_url,
        _parse_html,
    )
except ImportError:
    pass


class MadThemeSource(MadaraSource):
    use_legacy_api = False
    use_slug_search = False
    requests_per_minute = 5

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        response = await self._request(
            "GET",
            f"{self.base_url}/search",
            params={"q": query.strip(), "page": "1"},
        )
        response.raise_for_status()
        return self._listing(response.text, str(response.url))[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"}:
            return []
        response = await self._request(
            "GET",
            f"{self.base_url}/search",
            params={
                "q": "",
                "page": str(page),
                "sort": "views" if kind == "popular" else "updated_at",
            },
        )
        response.raise_for_status()
        return self._listing(response.text, str(response.url))

    def _listing(self, html: str, response_url: str) -> list[SourceSeries]:
        root = _parse_html(html)
        result: list[SourceSeries] = []
        for item in (
            node for node in root.descendants() if node.has_class("book-detailed-item")
        ):
            anchor = _first(
                item,
                lambda node: node.tag == "a" and bool(node.attrs.get("href")),
            )
            if anchor is None:
                continue
            title = anchor.attrs.get("title", "").strip() or anchor.text().strip()
            if title:
                result.append(
                    SourceSeries(
                        source_id=urljoin(response_url, anchor.attrs["href"]),
                        title=title,
                        source_name=self.name,
                    )
                )
        return result

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        title = series.title if isinstance(series, SourceSeries) else ""
        path = urlsplit(series_id).path.rstrip("/")
        slug = path.rsplit("/", 1)[-1]
        id_match = re.search(r"/manga/(\d+)-", path)
        manga_id = id_match.group(1) if id_match else ""
        if self.use_legacy_api and manga_id:
            url = f"{self.base_url}/service/backend/chaplist/"
            params = {"manga_id": manga_id, "manga_name": title}
        elif self.use_slug_search or manga_id:
            target = slug if self.use_slug_search else manga_id
            url = f"{self.base_url}/api/manga/{target}/chapters"
            params = {"source": "detail"}
        else:
            url = series_id
            params = {}
        response = await self._request("GET", url, params=params)
        response.raise_for_status()
        root = _parse_html(response.text)
        chapter_list = _first(root, lambda node: node.attrs.get("id") == "chapter-list")
        items = chapter_list.descendants("li") if chapter_list else []
        result: list[SourceChapter] = []
        for item in items:
            anchor = _first(
                item,
                lambda node: node.tag == "a" and bool(node.attrs.get("href")),
            )
            if anchor is None:
                continue
            label = _first(item, lambda node: node.has_class("chapter-title"))
            chapter_title = (label.text() if label else anchor.text()).strip()
            match = re.search(r"(\d+(?:\.\d+)?)", chapter_title)
            result.append(
                SourceChapter(
                    source_id=urljoin(str(response.url), anchor.attrs["href"]),
                    title=chapter_title or "Capítulo",
                    series_id=series_id,
                    source_name=self.name,
                    number=float(match.group(1)) if match else None,
                )
            )
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        response = await self._request("GET", chapter_id)
        response.raise_for_status()
        html = response.text
        manga_id = re.search(r"/manga/(\d+)-", str(response.url))
        chapter_id_match = re.search(r"chapterId\s*=\s*(\d+)", html)
        if manga_id and chapter_id_match:
            server = await self._request(
                "GET",
                f"{self.base_url}/service/backend/chapterServer/",
                params={"server_id": "1", "chapter_id": chapter_id_match.group(1)},
            )
            server.raise_for_status()
            html = server.text
        urls = self._page_urls(html, str(response.url))
        return [
            SourcePage(
                source_id=url,
                chapter_id=chapter_id,
                index=index,
                filename=url.split("||fallback=", 1)[0].rsplit("/", 1)[-1].split("?", 1)[0]
                or f"{index}.jpg",
                source_name=self.name,
            )
            for index, url in enumerate(urls, 1)
        ]

    def _page_urls(self, html: str, response_url: str) -> list[str]:
        js_images = re.search(r"var\s+chapImages\s*=\s*'([^']*)'", html)
        main_server = re.search(r'var\s+mainServer\s*=\s*"([^"]*)"', html)
        if js_images:
            paths = [path for path in js_images.group(1).split(",") if path]
            if main_server:
                host = main_server.group(1)
                if host.startswith("//"):
                    host = "https:" + host
                return [urljoin(host.rstrip("/") + "/", path.lstrip("/")) for path in paths]
            if paths and all(path.startswith(("http://", "https://")) for path in paths):
                return paths

        root = _parse_html(html)
        urls: list[str] = []
        for image in root.descendants("img"):
            if not (
                image.has_class("chapter-image")
                or self._has_ancestor_id(image, "chapter-images")
            ):
                continue
            primary = _image_url(image, response_url)
            if not primary:
                continue
            raw_fallback = re.search(r"this\.src='([^']+)'", image.attrs.get("onerror", ""))
            if raw_fallback:
                primary += f"||fallback={urljoin(response_url, raw_fallback.group(1))}"
            urls.append(primary)
        return urls

    async def page_bytes(self, page: SourcePage | str) -> SourcePageContent:
        value = page.source_id if isinstance(page, SourcePage) else page
        primary, _, fallback = value.partition("||fallback=")
        kwargs = {"headers": {"Referer": page.chapter_id}} if isinstance(page, SourcePage) else {}
        try:
            response = await self._request("GET", primary, **kwargs)
            response.raise_for_status()
        except Exception:
            if not fallback and "/res/" in primary:
                parts = urlsplit(primary)
                fallback = urlunsplit(
                    (parts.scheme, "sb.mbcdn.xyz", parts.path.replace("/res/", "/", 1), parts.query, "")
                )
            if not fallback:
                raise
            response = await self._request("GET", fallback, **kwargs)
            response.raise_for_status()
        return SourcePageContent(
            media_type=response.headers.get("Content-Type", "image/jpeg"),
            chunks=iter([response.content]),
        )

    @staticmethod
    def _has_ancestor_id(node: object, node_id: str) -> bool:
        parent = getattr(node, "parent", None)
        while parent is not None:
            if parent.attrs.get("id") == node_id:
                return True
            parent = parent.parent
        return False
