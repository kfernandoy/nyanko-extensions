"""Implementación común de los cuatro sitios FMReader."""

import base64
import re
from urllib.parse import urljoin

try:
    from .base import (
        FuenteBaseSource,
        SourceChapter,
        SourcePage,
        SourceSeries,
        _first,
        _image_url,
        _parse_html,
    )
except ImportError:
    pass


class FMReaderSource(FuenteBaseSource):
    profile = "regular"

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        response = await self._request(
            "GET",
            f"{self.base_url}/manga-list.html",
            params={"name": query.strip(), "page": "1"},
        )
        response.raise_for_status()
        return self._listing(response.text, str(response.url))[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"}:
            return []
        response = await self._request(
            "GET",
            f"{self.base_url}/manga-list.html",
            params={
                "listType": "pagination",
                "page": str(page),
                "sort": "views" if kind == "popular" else "last_update",
                "sort_type": "DESC",
            },
        )
        response.raise_for_status()
        return self._listing(response.text, str(response.url))

    def _listing(self, html: str, response_url: str) -> list[SourceSeries]:
        root = _parse_html(html)
        cards = (
            [node for node in root.descendants() if node.has_class("manga-card")]
            if self.profile == "nihon"
            else [
                node
                for node in root.descendants()
                if node.has_class("media") or node.has_class("thumb-item-flow")
            ]
        )
        result: list[SourceSeries] = []
        for card in cards:
            anchor = _first(
                card,
                lambda node: node.tag == "a"
                and bool(node.attrs.get("href"))
                and (node.has_class("manga-title") if self.profile == "nihon" else node.tag == "a"),
            )
            title = anchor.text().strip() if anchor else ""
            if anchor is not None and title:
                result.append(SourceSeries(urljoin(response_url, anchor.attrs["href"]), title, self.name))
        return result

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        if self.profile == "love":
            match = re.search(r"(\d+)/", series_id)
            url = f"{self.base_url}/app/manga/controllers/cont.Listchapter.php?mid={match.group(1)}" if match else ""
        elif self.profile in {"rawinu", "nihon"}:
            slug = series_id.split("/manga-", 1)[-1].split(".html", 1)[0]
            url = f"{self.base_url}/app/manga/controllers/cont.Listchapter.php?slug={slug}"
        else:
            url = series_id
        if not url:
            return []
        response = await self._request("GET", url)
        response.raise_for_status()
        root = _parse_html(response.text)
        if self.profile == "nihon":
            holder = _first(root, lambda node: node.has_class("at-series"))
            items = holder.descendants("a") if holder else []
        else:
            items = [
                node
                for node in root.descendants()
                if (
                    node.tag == "tr"
                    or (node.tag == "p" and node.parent and node.parent.attrs.get("id") == "list-chapters")
                )
                or (node.tag == "a" and node.parent and node.parent.has_class("list-chapters"))
            ]
        result: list[SourceChapter] = []
        for item in items:
            anchor = item if item.tag == "a" else _first(item, lambda node: node.tag == "a" and bool(node.attrs.get("href")))
            if anchor is None or not anchor.attrs.get("href"):
                continue
            title = (
                (_first(anchor, lambda node: node.has_class("chapter-name")) or anchor).text().strip()
                if self.profile == "nihon"
                else anchor.attrs.get("title", "").strip() or anchor.text().strip()
            )
            result.append(
                SourceChapter(
                    source_id=urljoin(self.base_url, anchor.attrs["href"]),
                    title=title or "Chapter",
                    series_id=series_id,
                    source_name=self.name,
                )
            )
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        response = await self._request("GET", chapter_id)
        response.raise_for_status()
        root = _parse_html(response.text)
        if self.profile in {"love", "rawinu"}:
            field = _first(root, lambda node: node.attrs.get("id") == "chapter")
            chapter_number = field.attrs.get("value", "") if field else ""
            endpoint = "cont.listImg.php" if self.profile == "love" else "cont.imagesChap.php"
            if not chapter_number:
                return []
            response = await self._request(
                "GET",
                f"{self.base_url}/app/manga/controllers/{endpoint}",
                params={"cid": chapter_number},
            )
            response.raise_for_status()
            root = _parse_html(response.text)
        images = [
            image
            for image in root.descendants("img")
            if image.has_class("chapter-img")
            or (self.profile == "nihon" and re.fullmatch(r"page\d+", image.attrs.get("id", "")))
        ]
        urls: list[str] = []
        for image in images:
            raw = next(
                (image.attrs[key] for key in ("data-img", "data-original", "data-src", "data-srcset", "data-aload", "src") if image.attrs.get(key)),
                "",
            )
            if self.profile == "welove" and "." not in raw:
                try:
                    url = base64.b64decode(raw).decode()
                except (ValueError, UnicodeDecodeError):
                    continue
            else:
                url = _image_url(image, str(response.url))
            if url:
                urls.append(url.strip("'"))
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
