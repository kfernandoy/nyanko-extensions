"""Implementación común de GoDa, incluida la API ofuscada de GoDa漫画."""

import base64
import json
import re
from urllib.parse import urljoin

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


def _decode_chapter_images(value: str) -> list[dict]:
    if not value.startswith("J7r") or not value.endswith("nQ"):
        return []
    body = value[3:-2]
    payload_length = len(body) - 5
    if payload_length <= 0:
        return []
    a_length = payload_length // 3
    b_length = (payload_length - a_length) // 2
    c_length = payload_length - a_length - b_length
    part1 = body[:b_length]
    if body[b_length : b_length + 2] != "kD":
        return []
    start = b_length + 2
    part2 = body[start : start + c_length]
    start += c_length
    if body[start : start + 3] != "W4s":
        return []
    part3 = body[start + 3 :]
    reordered = part3 + part1 + part2
    chunks = [
        reordered[index : index + 7]
        for index in range(0, len(reordered), 7)
    ]
    unzigzagged = "".join(
        chunk[::-1] if index % 2 else chunk for index, chunk in enumerate(chunks)
    )
    standard = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    custom = "_-9876543210abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    translated = unzigzagged.translate(str.maketrans(custom, standard))
    decoded = base64.urlsafe_b64decode(translated + "=" * (-len(translated) % 4))
    payload = json.loads(decoded)
    return payload if isinstance(payload, list) else []


class GodaSource(MadaraDetailsSource):
    profile = "regular"

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        response = await self._request(
            "GET",
            f"{self.base_url}/s/{query.strip()}",
            params={"page": "1"},
        )
        response.raise_for_status()
        return self._listing(response.text, str(response.url))[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"}:
            return []
        path = "hots" if kind == "popular" else "newss"
        response = await self._request("GET", f"{self.base_url}/{path}/page/{page}")
        response.raise_for_status()
        return self._listing(response.text, str(response.url))

    def _listing(self, html: str, response_url: str) -> list[SourceSeries]:
        root = _parse_html(html)
        result: list[SourceSeries] = []
        for card in (node for node in root.descendants() if node.has_class("pb-2")):
            anchor = _first(
                card,
                lambda node: node.tag == "a" and bool(node.attrs.get("href")),
            )
            heading = _first(card, lambda node: node.tag == "h3")
            title = heading.text().strip() if heading else ""
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
        details = await self._request("GET", series_id)
        details.raise_for_status()
        root = _parse_html(details.text)
        holder = _first(root, lambda node: node.attrs.get("id") == "mangachapters")
        manga_id = holder.attrs.get("data-mid", "") if holder else ""
        if not manga_id:
            return []
        if self.profile == "api":
            response = await self._request(
                "GET",
                "https://api-get-v3.mgsearcher.com/api/manga/get",
                params={"mid": manga_id, "mode": "all"},
            )
            response.raise_for_status()
            data = response.json().get("data", {})
            manga_slug = data.get("slug", "")
            result = []
            for item in reversed(data.get("chapters", [])):
                attrs = item.get("attributes", {})
                if not attrs.get("slug") or not item.get("id"):
                    continue
                result.append(
                    SourceChapter(
                        source_id=f"{self.base_url}/manga/{manga_slug}/{attrs['slug']}#{manga_id}/{item['id']}",
                        title=attrs.get("title") or "Chapter",
                        series_id=series_id,
                        source_name=self.name,
                    )
                )
            return result

        response = await self._request(
            "GET",
            f"{self.base_url}/manga/get",
            params={"mid": manga_id, "mode": "all"},
        )
        response.raise_for_status()
        root = _parse_html(response.text)
        result: list[SourceChapter] = []
        for item in (node for node in root.descendants() if node.has_class("chapteritem")):
            anchor = _first(
                item,
                lambda node: node.tag == "a" and bool(node.attrs.get("href")),
            )
            if anchor is None:
                continue
            result.append(
                SourceChapter(
                    source_id=f"{urljoin(str(response.url), anchor.attrs['href'])}#{manga_id}/{anchor.attrs.get('data-cs', '')}",
                    title=anchor.attrs.get("data-ct", "") or anchor.text().strip() or "Chapter",
                    series_id=series_id,
                    source_name=self.name,
                )
            )
        return list(reversed(result))

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        _, _, ids = chapter_id.rpartition("#")
        manga_id, _, page_id = ids.partition("/")
        if not manga_id or not page_id:
            return []
        if self.profile == "api":
            response = await self._request(
                "GET",
                "https://api-get-v3.mgsearcher.com/api/v2/chapter/getinfo",
                params={"m": manga_id, "c": page_id},
            )
            response.raise_for_status()
            encoded = (
                response.json()
                .get("data", {})
                .get("info", {})
                .get("images", {})
                .get("images", "")
            )
            rows = _decode_chapter_images(encoded)
            urls = [
                (int(item.get("order", index)), f"https://f40-1-4.g-mh.online{item['url']}")
                for index, item in enumerate(rows, 1)
                if item.get("url")
            ]
            urls.sort()
            page_urls = [url for _, url in urls]
        else:
            response = await self._request(
                "GET",
                f"{self.base_url}/chapter/getcontent",
                params={"m": manga_id, "c": page_id},
            )
            response.raise_for_status()
            root = _parse_html(response.text)
            holder = _first(root, lambda node: node.attrs.get("id") == "chapcontent")
            page_urls = [
                url
                for image in (holder.descendants("img") if holder else [])
                if (url := _image_url(image, str(response.url)))
            ]
        return [
            SourcePage(
                source_id=url,
                chapter_id=chapter_id,
                index=index,
                filename=url.rsplit("/", 1)[-1].split("?", 1)[0] or f"{index}.jpg",
                source_name=self.name,
            )
            for index, url in enumerate(page_urls, 1)
        ]
