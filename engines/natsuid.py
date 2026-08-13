"""Implementación común de sitios NatsuId para Nyanko Source v4."""

import re
from urllib.parse import parse_qs, urljoin, urlsplit

try:
    from .madara_details import (
        MadaraDetailsSource,
        SourceChapter,
        SourcePage,
        SourceSeries,
        _first,
        _image_url,
        _parse_html,
    )
except ImportError:
    pass


class NatsuIdSource(MadaraDetailsSource):
    chapter_page = "999"

    def __init__(self, fetcher=None) -> None:
        super().__init__(fetcher)
        self._nonce = ""

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        return (await self._listing(query.strip(), "relevance", 1))[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"}:
            return []
        return await self._listing("", "popular" if kind == "popular" else "date", page)

    async def _listing(self, query: str, order_by: str, page: int) -> list[SourceSeries]:
        nonce = await self._search_nonce()
        response = await self._request(
            "POST",
            f"{self.base_url}/wp-admin/admin-ajax.php",
            params={"action": "advanced_search"},
            data={
                "nonce": nonce,
                "inclusion": "OR",
                "exclusion": "OR",
                "page": str(page),
                "genre": "[]",
                "genre_exclude": "[]",
                "author": "[]",
                "artist": "[]",
                "project": "0",
                "type": "[]",
                "status": "[]",
                "order": "desc",
                "orderby": order_by,
                "query": query,
            },
        )
        response.raise_for_status()
        root = _parse_html(response.text)
        result: list[SourceSeries] = []
        seen: set[str] = set()
        for anchor in root.descendants("a"):
            href = anchor.attrs.get("href", "")
            image = _first(anchor, lambda node: node.tag == "img")
            if "/manga/" not in href or image is None:
                continue
            source_id = urljoin(str(response.url), href)
            title = (
                anchor.text().strip()
                or image.attrs.get("alt", "").strip()
                or image.attrs.get("title", "").strip()
            )
            if source_id not in seen and title:
                seen.add(source_id)
                result.append(
                    SourceSeries(
                        source_id=source_id,
                        title=title,
                        source_name=self.name,
                        cover_url=_image_url(image, str(response.url)),
                    )
                )
        return result

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        slug = urlsplit(series_id).path.rstrip("/").rsplit("/", 1)[-1]
        response = await self._request(
            "GET",
            f"{self.base_url}/wp-json/wp/v2/manga",
            params={"slug[]": slug, "per_page": "2", "_embed": ""},
        )
        response.raise_for_status()
        items = response.json()
        item = items[0] if isinstance(items, list) and items else None
        if not isinstance(item, dict):
            return series if isinstance(series, SourceSeries) else SourceSeries(series_id, slug, self.name)
        embedded = item.get("_embedded", {})
        terms = embedded.get("wp:term", []) if isinstance(embedded, dict) else []

        def taxonomy(name: str) -> list[str]:
            return [
                str(term.get("name", "")).strip()
                for group in terms if isinstance(group, list)
                for term in group if isinstance(term, dict) and term.get("taxonomy") == name
                and str(term.get("name", "")).strip()
            ]

        media = embedded.get("wp:featuredmedia", []) if isinstance(embedded, dict) else []
        cover_url = next(
            (str(value.get("source_url")) for value in media if isinstance(value, dict) and value.get("source_url")),
            None,
        )
        title = str(item.get("title", {}).get("rendered", "")).strip()
        content = str(item.get("content", {}).get("rendered", ""))
        description = _parse_html(content).text().strip()
        statuses = taxonomy("status")
        return SourceSeries(
            source_id=series_id,
            title=title or (series.title if isinstance(series, SourceSeries) else slug),
            source_name=self.name,
            cover_url=cover_url or (series.cover_url if isinstance(series, SourceSeries) else None),
            description=description or None,
            author=", ".join(taxonomy("series-author")) or None,
            artist=", ".join(taxonomy("artist")) or None,
            status=self._madara_status(statuses[0]) if statuses else None,
            content_tags=tuple(dict.fromkeys(taxonomy("genre") + taxonomy("type"))),
            metadata=series.metadata if isinstance(series, SourceSeries) else {},
            web_url=series_id,
        )

    async def _search_nonce(self) -> str:
        if self._nonce:
            return self._nonce
        response = await self._request(
            "GET",
            f"{self.base_url}/wp-admin/admin-ajax.php",
            params={"type": "search_form", "action": "get_nonce"},
        )
        response.raise_for_status()
        root = _parse_html(response.text)
        field = _first(
            root,
            lambda node: node.tag == "input" and node.attrs.get("name") == "search_nonce",
        )
        self._nonce = field.attrs.get("value", "") if field else ""
        if not self._nonce:
            raise ValueError("NatsuId no entregó el nonce de búsqueda")
        return self._nonce

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        details = await self._request("GET", series_id)
        details.raise_for_status()
        root = _parse_html(details.text)
        gallery = _first(root, lambda node: node.attrs.get("id") == "gallery-list")
        query = parse_qs(urlsplit(gallery.attrs.get("hx-get", "") if gallery else "").query)
        manga_id = next(iter(query.get("manga_id", [])), "")
        if not manga_id:
            match = re.search(r"manga_id=(\d+)", details.text)
            manga_id = match.group(1) if match else ""
        if not manga_id:
            return []
        response = await self._request(
            "GET",
            f"{self.base_url}/wp-admin/admin-ajax.php",
            params={
                "manga_id": manga_id,
                "page": self.chapter_page,
                "action": "chapter_list",
            },
        )
        response.raise_for_status()
        root = _parse_html(response.text)
        result: list[SourceChapter] = []
        for anchor in root.descendants("a"):
            href = anchor.attrs.get("href", "")
            if not href or _first(anchor, lambda node: node.tag == "time") is None:
                continue
            label = _first(anchor, lambda node: node.tag == "span")
            title = (label.text() if label else anchor.text()).strip()
            match = re.search(r"(\d+(?:\.\d+)?)", title)
            result.append(
                SourceChapter(
                    source_id=urljoin(str(response.url), href),
                    title=title or "Capítulo",
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
        root = _parse_html(response.text)
        urls = [
            url
            for image in root.descendants("img")
            if self._has_ancestor_tag(image, "main")
            and self._has_ancestor_tag(image, "section")
            and (url := _image_url(image, str(response.url)))
        ]
        return [
            SourcePage(
                source_id=url,
                chapter_id=chapter_id,
                index=index,
                filename=url.rsplit("/", 1)[-1].split("?", 1)[0] or f"{index}.jpg",
                source_name=self.name,
            )
            for index, url in enumerate(dict.fromkeys(urls), 1)
        ]

    @staticmethod
    def _has_ancestor_tag(node: object, tag: str) -> bool:
        parent = getattr(node, "parent", None)
        while parent is not None:
            if parent.tag == tag:
                return True
            parent = parent.parent
        return False
