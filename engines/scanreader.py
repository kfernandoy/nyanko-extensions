"""Implementación común de Scan Reader."""

import base64
import json
import re
from urllib.parse import urljoin

try:
    from .base import (
        FuenteBaseSource,
        SourceChapter,
        SourcePage,
        SourceSeries,
        _first,
        _parse_html,
    )
except ImportError:
    pass


class ScanReaderSource(FuenteBaseSource):
    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        response = await self._request(
            "GET",
            self.base_url,
            params={"s": query.strip(), "post_type": "manga"},
        )
        response.raise_for_status()
        return self._cards(response.text, str(response.url))[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"}:
            return []
        if kind == "latest":
            url = f"{self.base_url}/dernieres-sorties/page/{page}/"
        elif page == 1:
            url = self.base_url
        else:
            url = f"{self.base_url}/bibliotheque/page/{page - 1}/?sort=views"
        response = await self._request("GET", url)
        response.raise_for_status()
        return self._latest(response.text, str(response.url)) if kind == "latest" else self._cards(response.text, str(response.url))

    def _cards(self, html: str, response_url: str) -> list[SourceSeries]:
        root = _parse_html(html)
        result: list[SourceSeries] = []
        for card in (node for node in root.descendants() if node.has_class("manga-card")):
            heading = _first(card, lambda node: node.tag == "h3")
            anchor = _first(card, lambda node: node.tag == "a" and bool(node.attrs.get("href")))
            title = heading.text().strip() if heading else ""
            if anchor is not None and title and "(Novel)" not in title:
                result.append(
                    SourceSeries(
                        source_id=urljoin(response_url, anchor.attrs["href"]),
                        title=title,
                        source_name=self.name,
                    )
                )
        return result

    def _latest(self, html: str, response_url: str) -> list[SourceSeries]:
        root = _parse_html(html)
        result: list[SourceSeries] = []
        for cover in (node for node in root.descendants() if node.has_class("manga-cover")):
            anchor = _first(cover, lambda node: node.tag == "a" and bool(node.attrs.get("href")))
            parent = cover.parent
            heading = _first(parent or cover, lambda node: node.tag == "h3" and node.has_class("manga-title-display"))
            title = heading.text().strip() if heading else ""
            if anchor is not None and title and "(Novel)" not in title:
                result.append(SourceSeries(urljoin(response_url, anchor.attrs["href"]), title, self.name))
        return result

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        response = await self._request("GET", series_id)
        response.raise_for_status()
        root = _parse_html(response.text)
        container = _first(root, lambda node: node.attrs.get("id") == "secure-chapters-container")
        if container is None:
            return []
        manga_id = container.attrs.get("data-manga-id", "")
        nonce = container.attrs.get("data-nonce", "")
        if not manga_id or not nonce:
            return []
        response = await self._request(
            "POST",
            f"{self.base_url}/wp-admin/admin-ajax.php",
            data={"action": "load_protected_chapters_html", "manga_id": manga_id, "nonce": nonce},
            headers={"Referer": series_id, "X-Requested-With": "XMLHttpRequest"},
        )
        response.raise_for_status()
        html = response.text
        try:
            html = json.loads(html).get("data") or html
        except (json.JSONDecodeError, AttributeError):
            pass
        if html.strip() in {"0", "-1"}:
            return []
        root = _parse_html(html)
        result: list[SourceChapter] = []
        for heading in root.descendants("h4"):
            anchor = None
            node = heading
            while node.parent is not None and anchor is None:
                node = node.parent
                anchor = _first(
                    node,
                    lambda item: item.tag == "a"
                    and "/chapitre/" in item.attrs.get("href", ""),
                )
            if anchor is not None:
                result.append(
                    SourceChapter(
                        source_id=urljoin(series_id, anchor.attrs["href"]),
                        title=heading.text().strip() or "Chapitre",
                        series_id=series_id,
                        source_name=self.name,
                    )
                )
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        response = await self._request("GET", chapter_id)
        response.raise_for_status()
        match = re.search(
            r'(?:const|let|var)\s+\w+\s*=\s*\[((?:\s*"[A-Za-z0-9+/=]+"(?:\s*,\s*)?)+)\s*]',
            response.text,
        )
        if match is None:
            return []
        urls = [
            base64.b64decode(value).decode()[::-1]
            for value in re.findall(r'"([A-Za-z0-9+/=]{20,})"', match.group(1))
        ]
        return [
            SourcePage(
                source_id=url,
                chapter_id=chapter_id,
                index=index,
                filename=url.rsplit("/", 1)[-1].split("?", 1)[0] or f"{index}.jpg",
                source_name=self.name,
            )
            for index, url in enumerate(urls, 1)
        ]
