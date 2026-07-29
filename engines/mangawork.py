"""Implementación común de MangaWork."""

import re
from urllib.parse import urljoin

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


class MangaWorkSource(MadaraSource):
    series_path = "series"
    requests_per_minute = 120

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        response = await self._request(
            "GET",
            f"{self.base_url}/{self.series_path}/",
            params={"title": query.strip(), "order": "title", "status": "", "type": ""},
        )
        response.raise_for_status()
        return self._listing(response.text, str(response.url))[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"}:
            return []
        path = f"{self.series_path}/" + (f"page/{page}/" if page > 1 else "")
        response = await self._request(
            "GET",
            f"{self.base_url}/{path}",
            params={"title": "", "order": "popular" if kind == "popular" else "update", "status": "", "type": ""},
        )
        response.raise_for_status()
        return self._listing(response.text, str(response.url))

    def _listing(self, html: str, response_url: str) -> list[SourceSeries]:
        root = _parse_html(html)
        result: list[SourceSeries] = []
        for card in (
            node
            for node in root.descendants("div")
            if node.has_class("w-full") and node.has_class("h-full")
        ):
            anchor = _first(
                card,
                lambda node: node.tag == "a"
                and "/manga/" in node.attrs.get("href", ""),
            )
            heading = _first(card, lambda node: node.tag == "h1")
            if anchor is None:
                continue
            title = anchor.attrs.get("title", "").strip() or (heading.text().strip() if heading else "")
            if title:
                result.append(SourceSeries(urljoin(response_url, anchor.attrs["href"]), title, self.name))
        return result

    def _parse_chapters(self, html: str, response_url: str, series_id: str) -> tuple[list[SourceChapter], object | None]:
        root = _parse_html(html)
        holder = _first(root, lambda node: node.attrs.get("id") == "chapter_list")
        result: list[SourceChapter] = []
        for item in holder.descendants("li") if holder else []:
            anchor = _first(item, lambda node: node.tag == "a" and bool(node.attrs.get("href")))
            if anchor is None:
                continue
            name_node = _first(
                item,
                lambda node: node.tag == "span" and (node.has_class("m-0") or node.has_class("line-clamp-1")),
            )
            title = name_node.text().strip() if name_node else anchor.text().strip() or "Chapter"
            match = re.search(r"(\d+(?:[.,]\d+)?)", title)
            result.append(
                SourceChapter(
                    source_id=urljoin(response_url, anchor.attrs["href"]),
                    title=title,
                    series_id=series_id,
                    source_name=self.name,
                    number=float(match.group(1).replace(",", ".")) if match else None,
                )
            )
        next_button = _first(
            root,
            lambda node: node.tag == "button"
            and node.has_class("load-chapters")
            and bool(node.attrs.get("data-paged")),
        )
        return result, next_button

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        response = await self._request("GET", series_id)
        response.raise_for_status()
        chapters, button = self._parse_chapters(response.text, str(response.url), series_id)
        holder = _first(_parse_html(response.text), lambda node: node.attrs.get("id") == "chapter_list")
        post_id = holder.attrs.get("data-post-id", "") if holder else ""
        count = holder.attrs.get("data-count", "1000") if holder else "1000"
        requested: set[str] = set()
        while button is not None and post_id:
            page = button.attrs.get("data-paged", "")
            if not page or page in requested:
                break
            requested.add(page)
            response = await self._request(
                "POST",
                f"{self.base_url}/wp-admin/admin-ajax.php",
                data={
                    "action": "load_chapters",
                    "post_id": post_id,
                    "count": count,
                    "paged": page,
                    "order": button.attrs.get("data-order", "DESC"),
                },
                headers={"Referer": series_id, "Origin": self.base_url},
            )
            response.raise_for_status()
            extra, button = self._parse_chapters(response.text, str(response.url), series_id)
            chapters.extend(extra)
        return list({chapter.source_id: chapter for chapter in chapters}.values())

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        response = await self._request("GET", chapter_id)
        response.raise_for_status()
        root = _parse_html(response.text)
        urls = [
            url
            for image in root.descendants("img")
            if (
                image.attrs.get("id") == "imagech"
                or "/manga_auto_capitulos/" in image.attrs.get("src", "")
            )
            and (url := _image_url(image, str(response.url)))
        ]
        if not urls:
            urls = [
                value.replace("\\/", "/")
                for value in re.findall(r'"image"\s*:\s*"([^"]+)"', response.text)
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
