"""Implementación común de sitios Liliana para Nyanko Source v3."""

import json
import re
from urllib.parse import urljoin

try:
    from .madara import (
        MadaraSource,
        SourceChapter,
        SourcePage,
        SourceSeries,
        _Node,
        _first,
        _image_url,
        _parse_html,
    )
except ImportError:
    pass


class LilianaSource(MadaraSource):
    profile = "default"

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        if self.profile == "dokiraw":
            response = await self._request(
                "GET",
                f"{self.base_url}/search/manga",
                params={"keyword": query.strip(), "page": "1"},
            )
        else:
            response = await self._request(
                "GET",
                f"{self.base_url}/search/1/",
                params={"keyword": query.strip()},
            )
        response.raise_for_status()
        return self._listing(response.text, str(response.url))[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"} or (kind == "latest" and self.profile == "dokiraw"):
            return []
        if self.profile == "dokiraw":
            response = await self._request("GET", f"{self.base_url}/hot", params={"page": str(page)})
        elif kind == "popular":
            response = await self._request("GET", f"{self.base_url}/ranking/week/{page}")
        else:
            response = await self._request(
                "GET",
                f"{self.base_url}/all-manga/{page}/",
                params={"sort": "last_update", "status": "0"},
            )
        response.raise_for_status()
        return self._listing(response.text, str(response.url))

    def _listing(self, html: str, response_url: str) -> list[SourceSeries]:
        root = _parse_html(html)
        if self.profile == "dokiraw":
            items = [
                node
                for node in root.descendants()
                if any(name.startswith("manga-item_item") for name in node.attrs.get("class", "").split())
            ]
        else:
            items = [
                child
                for grid in (node for node in root.descendants() if node.has_class("grid"))
                if self._has_ancestor_id(grid, "main")
                for child in grid.children
                if isinstance(child, _Node) and child.tag == "div"
            ]
        result: list[SourceSeries] = []
        for item in items:
            if self.profile == "dokiraw":
                anchor = _first(
                    item,
                    lambda node: node.tag == "a" and "/manga/" in node.attrs.get("href", ""),
                )
                heading = _first(item, lambda node: node.tag == "h3")
                title = heading.text().strip() if heading else ""
            else:
                center = _first(item, lambda node: node.has_class("text-center"))
                anchor = _first(
                    center or item,
                    lambda node: node.tag == "a" and bool(node.attrs.get("href")),
                )
                title = anchor.text().strip() if anchor else ""
            if anchor is not None and title:
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
        response = await self._request("GET", series_id)
        response.raise_for_status()
        root = _parse_html(response.text)
        if self.profile == "dokiraw":
            items = [
                anchor
                for anchor in root.descendants("a")
                if _first(
                    anchor,
                    lambda node: any(
                        name.startswith("manga-detail_chapter")
                        for name in node.attrs.get("class", "").split()
                    ),
                )
                is not None
            ]
        else:
            items = [node for node in root.descendants("li") if node.has_class("chapter")]
        result: list[SourceChapter] = []
        for item in items:
            anchor = item if item.tag == "a" else _first(
                item,
                lambda node: node.tag == "a" and bool(node.attrs.get("href")),
            )
            if anchor is None:
                continue
            if self.profile == "dokiraw":
                container = _first(
                    anchor,
                    lambda node: any(
                        name.startswith("manga-detail_chapter")
                        for name in node.attrs.get("class", "").split()
                    ),
                )
                label = _first(container, lambda node: node.tag == "span") if container else None
                title = label.text().strip() if label else anchor.text().strip()
            else:
                title = anchor.text().strip()
            match = re.search(r"(\d+(?:\.\d+)?)", title)
            result.append(
                SourceChapter(
                    source_id=urljoin(str(response.url), anchor.attrs.get("href", "")),
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
        if self.profile == "dokiraw":
            root = _parse_html(response.text)
            containers = [node for node in root.descendants() if node.has_class("page-chapter")]
            urls = [
                url
                for container in containers
                for image in container.descendants("img")
                if (url := self._dokiraw_image(image, str(response.url)))
            ]
        else:
            match = re.search(r"const\s+CHAPTER_ID\s*=\s*([^;]+)", response.text)
            if match is None:
                return []
            chapter_key = match.group(1).strip().strip("'\"")
            ajax = await self._request(
                "GET",
                f"{self.base_url}/ajax/image/list/chap/{chapter_key}",
                headers={"Referer": str(response.url), "X-Requested-With": "XMLHttpRequest"},
            )
            ajax.raise_for_status()
            try:
                payload = ajax.json()
            except (AttributeError, json.JSONDecodeError):
                payload = json.loads(ajax.text)
            if not payload.get("status"):
                return []
            urls = self._separator_urls(payload.get("html", ""), str(response.url))
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

    def _separator_urls(self, html: str, response_url: str) -> list[str]:
        root = _parse_html(html)
        rows: list[tuple[int, str]] = []
        for position, separator in enumerate(
            (node for node in root.descendants() if node.has_class("separator")),
            1,
        ):
            anchor = _first(
                separator,
                lambda node: node.tag == "a" and bool(node.attrs.get("href")),
            )
            if anchor is None:
                continue
            url = urljoin(response_url, anchor.attrs["href"])
            clean = url.lower().split("?", 1)[0].split("#", 1)[0]
            if clean.endswith(".svg") or "loading_comments" in url.lower():
                continue
            raw_index = separator.attrs.get("data-index", "")
            rows.append((int(raw_index) if raw_index.isdigit() else position, url))
        return [url for _, url in sorted(rows)]

    @staticmethod
    def _dokiraw_image(image: _Node, response_url: str) -> str:
        for key in ("data-cdn", "data-original"):
            if image.attrs.get(key):
                return urljoin(response_url, image.attrs[key])
        return _image_url(image, response_url)

    @staticmethod
    def _has_ancestor_id(node: object, node_id: str) -> bool:
        parent = getattr(node, "parent", None)
        while parent is not None:
            if parent.attrs.get("id") == node_id:
                return True
            parent = parent.parent
        return False
