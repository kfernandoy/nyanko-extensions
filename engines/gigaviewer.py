"""Motor común para sitios japoneses GigaViewer."""

import io
import json
import math
import re
from urllib.parse import urljoin, urlparse, urlunparse

from PIL import Image

try:
    from .madara import MadaraSource, SourceChapter, SourcePage, SourcePageContent, SourceSeries, _first, _parse_html
except ImportError:
    pass


class GigaViewerSource(MadaraSource):
    supports_latest = True

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        response = await self._request("GET", f"{self.base_url}/search", params={"q": query.strip()})
        if getattr(response, "status_code", 200) == 404:
            return []
        response.raise_for_status()
        return self._series_list(response.text)[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"} or (kind == "latest" and not self.supports_latest):
            return []
        response = await self._request("GET", f"{self.base_url}/series")
        response.raise_for_status()
        return self._series_list(response.text)

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        marker = _first(
            root,
            lambda node: node.attrs.get("data-giga_series")
            or node.attrs.get("data-aggregate-id"),
        )
        aggregate_id = (
            marker.attrs.get("data-giga_series", "") or marker.attrs.get("data-aggregate-id", "")
            if marker
            else ""
        )
        if not aggregate_id:
            return []
        result: list[SourceChapter] = []
        for item_type in ("episode", "volume"):
            offset = 0
            while True:
                page = await self._request(
                    "GET",
                    f"{self.base_url}/api/viewer/pagination_readable_products",
                    params={
                        "type": item_type,
                        "aggregate_id": aggregate_id,
                        "sort_order": "desc",
                        "offset": str(offset),
                    },
                    headers={"Referer": str(response.url)},
                )
                page.raise_for_status()
                values = page.json()
                if not values:
                    break
                for item in values:
                    title = str(item.get("title") or "Capítulo")
                    item_id = item.get("readable_product_id")
                    if item_id:
                        result.append(
                            SourceChapter(
                                source_id=f"{self.base_url}/{item_type}/{item_id}",
                                title=f"(Volume) {title}" if item_type == "volume" else title,
                                series_id=series_id,
                                source_name=self.name,
                                number=self._chapter_number(title),
                            )
                        )
                offset += len(values)
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        response = await self._request("GET", urljoin(f"{self.base_url}/", chapter_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        script = _first(root, lambda node: node.tag == "script" and node.attrs.get("id") == "episode-json")
        if script is None:
            return []
        try:
            payload = json.loads(script.attrs.get("data-value", ""))
        except json.JSONDecodeError:
            return []
        structure = payload.get("readableProduct", {}).get("pageStructure") or {}
        scrambled = structure.get("choJuGiga") == "baku"
        return [
            SourcePage(
                source_id=f"{item['src']}#scramble" if scrambled else item["src"],
                chapter_id=chapter_id,
                index=index,
                filename=f"{index}.jpg",
                source_name=self.name,
            )
            for index, item in enumerate(structure.get("pages", []), 1)
            if item.get("type") == "main" and item.get("src")
        ]

    async def page_bytes(self, page: SourcePage | str) -> SourcePageContent:
        value = page.source_id if isinstance(page, SourcePage) else page
        parsed = urlparse(value)
        response = await self._request("GET", urlunparse(parsed._replace(fragment="")))
        response.raise_for_status()
        content = response.content
        if parsed.fragment == "scramble":
            source = Image.open(io.BytesIO(content)).convert("RGB")
            output = source.copy()
            width = math.floor(source.width / 32) * 8
            height = math.floor(source.height / 32) * 8
            for origin in range(16):
                destination = origin % 4 * 4 + origin // 4
                sx, sy = origin % 4 * width, origin // 4 * height
                dx, dy = destination % 4 * width, destination // 4 * height
                output.paste(source.crop((sx, sy, sx + width, sy + height)), (dx, dy))
            buffer = io.BytesIO()
            output.save(buffer, "JPEG", quality=90)
            content = buffer.getvalue()
        return SourcePageContent(media_type="image/jpeg", chunks=iter([content]))

    def _series_list(self, html: str) -> list[SourceSeries]:
        root = _parse_html(html)
        result: list[SourceSeries] = []
        seen: set[str] = set()
        for anchor in root.descendants("a"):
            href = anchor.attrs.get("href", "")
            if "/series/" not in href:
                continue
            title_node = _first(anchor, lambda node: "title" in node.attrs.get("class", ""))
            title = (title_node.text() if title_node else anchor.text()).strip()
            source_id = urljoin(f"{self.base_url}/", href)
            if title and source_id not in seen:
                seen.add(source_id)
                result.append(SourceSeries(source_id, title, self.name))
        return result

    @staticmethod
    def _chapter_number(title: str) -> float | None:
        found = re.search(r"\d+(?:\.\d+)?", title)
        return float(found.group()) if found else None
