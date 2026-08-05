"""Implementación común de sitios GroupLe para Nyanko Source v4."""

import re
from urllib.parse import urljoin

try:
    from .madara import (
        MadaraSource,
        SourceChapter,
        SourcePage,
        SourceSeries,
        _first,
        _parse_html,
    )
except ImportError:
    pass


class GroupLeSource(MadaraSource):
    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        response = await self._request(
            "GET",
            f"{self.base_url}/search/advancedResults",
            params={"offset": "0", "q": query.strip()},
        )
        response.raise_for_status()
        return self._listing(response.text, str(response.url))[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"}:
            return []
        response = await self._request(
            "GET",
            f"{self.base_url}/list",
            params={
                "sortType": "rate" if kind == "popular" else "updated",
                "offset": str(50 * max(page - 1, 0)),
            },
        )
        response.raise_for_status()
        return self._listing(response.text, str(response.url))

    def _listing(self, html: str, response_url: str) -> list[SourceSeries]:
        root = _parse_html(html)
        result: list[SourceSeries] = []
        for tile in (node for node in root.descendants() if node.has_class("tile")):
            heading = _first(tile, lambda node: node.tag == "h3")
            anchor = _first(
                heading or tile,
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
        response = await self._request("GET", series_id)
        response.raise_for_status()
        root = _parse_html(response.text)
        user_hash = re.search(r"user_hash.+?'([^']+)'", response.text)
        suffix = f"?d={user_hash.group(1)}&mtr=true" if user_hash else "?mtr=true"
        result: list[SourceChapter] = []
        for row in (node for node in root.descendants("tr") if node.has_class("item-row")):
            anchor = _first(row, lambda node: node.tag == "a" and node.has_class("chapter-link"))
            if anchor is None or not anchor.attrs.get("href"):
                continue
            title = anchor.text().removesuffix(" новое").strip()
            number_node = _first(row, lambda node: node.has_class("item-title"))
            raw_number = number_node.attrs.get("data-num", "") if number_node else ""
            number = float(raw_number) / 10 if raw_number.isdigit() else None
            scanlator = (
                anchor.attrs.get("title", "")
                .replace("(Переводчик),", "&")
                .removesuffix(" (Переводчик)")
            )
            result.append(
                SourceChapter(
                    source_id=urljoin(str(response.url), anchor.attrs["href"]) + suffix,
                    title=title or "Capítulo",
                    series_id=series_id,
                    source_name=self.name,
                    number=number,
                    scanlator=scanlator,
                )
            )
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        response = await self._request("GET", chapter_id)
        response.raise_for_status()
        marker = next(
            (value for value in ("rm_h.readerInit(", "rm_h.readerDoInit(") if value in response.text),
            "",
        )
        if not marker:
            return []
        payload = response.text.split(marker, 1)[1].split(");", 1)[0]
        urls: list[str] = []
        for first, second, third in re.findall(r"'(.*?)','(.*?)',\"(.*?)\"", payload):
            if not second and third.startswith("/static/"):
                url = self.base_url + third
            else:
                url = first + third if second.endswith("/manga/") else second + first + third
            if "://" not in url:
                url = "https:" + url
            if "one-way.work" in url:
                url = url.split("?", 1)[0]
            urls.append(url.replace("//resh", "//h"))
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
