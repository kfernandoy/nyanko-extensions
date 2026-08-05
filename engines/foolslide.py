"""Implementación común de sitios FoolSlide para Nyanko Source v4."""

import json
import re
from datetime import datetime, timedelta
from urllib.parse import urljoin

try:
    from .madara import (
        MadaraSource,
        SourceChapter,
        SourcePage,
        SourcePreference,
        SourceSeries,
        _first,
        _parse_html,
    )
except ImportError:
    pass


class FoolSlideSource(MadaraSource):
    url_modifier = ""
    profile = "default"

    def get_preferences(self) -> list[SourcePreference]:
        return [SourcePreference("adult", "Show adult content", "checkbox", default=True)]

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        response = await self._request(
            "POST",
            f"{self.base_url}{self.url_modifier}/search/",
            data={"search": query.strip()},
        )
        response.raise_for_status()
        return self._listing(response.text, str(response.url))[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"}:
            return []
        if self.profile == "juinjutsu" and kind == "latest":
            return []
        path = "latest" if self.profile == "juinjutsu" else "directory" if kind == "popular" else "latest"
        response = await self._request(
            "GET",
            f"{self.base_url}{self.url_modifier}/{path}/{page}/",
        )
        response.raise_for_status()
        return self._listing(response.text, str(response.url))

    def _listing(self, html: str, response_url: str) -> list[SourceSeries]:
        root = _parse_html(html)
        result: list[SourceSeries] = []
        seen: set[str] = set()
        for group in (node for node in root.descendants() if node.has_class("group")):
            anchor = _first(
                group,
                lambda node: node.tag == "a"
                and bool(node.attrs.get("href"))
                and "title" in node.attrs,
            )
            if anchor is None:
                continue
            source_id = urljoin(response_url, anchor.attrs["href"])
            title = anchor.text().strip() or anchor.attrs.get("title", "").strip()
            if source_id in seen or not title:
                continue
            seen.add(source_id)
            image = _first(group, lambda node: node.tag == "img")
            cover = urljoin(response_url, image.attrs.get("src", "")).replace("/thumb_", "/") if image else None
            result.append(SourceSeries(
                source_id=source_id,
                title=title,
                source_name=self.name,
                cover_url=cover,
                web_url=source_id,
            ))
        if self.profile == "juinjutsu":
            return [chapter for chapter in result if not chapter.title.strip().isdigit()]
        return result

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request("POST", series_id, data={"adult": "true"})
        response.raise_for_status()
        root = _parse_html(response.text)

        def field(labels: str) -> str | None:
            match = re.search(rf"(?is)<b[^>]*>\s*(?:{labels})\s*</b>\s*:?\s*([^<]*)", response.text)
            return match.group(1).strip() or None if match else None

        image = _first(
            root,
            lambda node: node.tag == "img"
            and (self._has_class_ancestor(node, "thumbnail") or self._has_class_ancestor(node, "thumb")),
        )
        title = series.title if isinstance(series, SourceSeries) else series_id.rstrip("/").rsplit("/", 1)[-1]
        return SourceSeries(
            source_id=series_id,
            title=title,
            source_name=self.name,
            cover_url=urljoin(str(response.url), image.attrs.get("src", "")) if image else None,
            description=field("Synopsis|Description|Trama"),
            author=field("Author|Autore"),
            artist=field("Artist"),
            metadata=series.metadata if isinstance(series, SourceSeries) else {},
            web_url=str(response.url),
        )

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        response = await self._request("POST", series_id, data={"adult": "true"})
        response.raise_for_status()
        root = _parse_html(response.text)
        result: list[SourceChapter] = []
        for item in (node for node in root.descendants() if node.has_class("element")):
            anchor = _first(
                item,
                lambda node: node.tag == "a"
                and bool(node.attrs.get("href"))
                and "title" in node.attrs,
            )
            if anchor is None:
                continue
            title = anchor.text().strip()
            match = re.search(r"(\d+(?:\.\d+)?)", title)
            date = _first(item, lambda node: node.has_class("meta_r"))
            date_text = date.text().split(", ", 1)[-1] if date else ""
            result.append(
                SourceChapter(
                    source_id=urljoin(str(response.url), anchor.attrs["href"]),
                    title=title or "Capítulo",
                    series_id=series_id,
                    source_name=self.name,
                    number=float(match.group(1)) if match else None,
                    language=self.language,
                    uploaded_at=self._date(date_text),
                )
            )
        return result

    def _date(self, value: str) -> str | None:
        text = value.strip()
        if parsed := self._madara_date(text):
            return parsed
        if text.casefold().startswith("tomorrow"):
            return (datetime.now() + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        for pattern, cleaned in (
            ("%Y.%m.%d", text),
            ("%d %B, %Y", re.sub(r"(?<=\d)(?:st|nd|rd|th)", "", text, flags=re.I)),
        ):
            try:
                return datetime.strptime(cleaned, pattern).isoformat()
            except ValueError:
                pass
        without_year = re.sub(r"(?<=\d)(?:st|nd|rd|th)", "", text, flags=re.I)
        try:
            parsed = datetime.strptime(without_year, "%d %B").replace(year=datetime.now().year)
            return parsed.isoformat()
        except ValueError:
            return None

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        response = await self._request("POST", chapter_id, data={"adult": "true"})
        response.raise_for_status()
        match = re.search(r"var\s+pages\s*=\s*(\[.*?])\s*;", response.text, re.S)
        if match is None:
            return []
        try:
            raw = json.loads(match.group(1))
        except json.JSONDecodeError:
            return []
        urls = [urljoin(str(response.url), page["url"]) for page in raw if page.get("url")]
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
