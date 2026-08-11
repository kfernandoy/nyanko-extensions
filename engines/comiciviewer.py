"""Motor común para los portales japoneses basados en ComiciViewer."""

import io
import json
import re
from urllib.parse import urljoin, urlparse, urlunparse

from PIL import Image

try:
    from .base import (
        FuenteBaseSource,
        SourceChapter,
        SourcePage,
        SourcePageContent,
        SourceSeries,
        _first,
        _parse_html,
    )
except ImportError:
    pass


class ComiciViewerSource(FuenteBaseSource):
    api_url = ""
    latest_path = ""
    supports_latest = True

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        response = await self._request(
            "GET",
            f"{self.api_url or self.base_url + '/api'}/search",
            params={"q": query.strip(), "page": "1", "size": str(limit)},
        )
        response.raise_for_status()
        payload = response.json()
        values = payload.get("searchResult", {}).get("series", {}).get("series", [])
        return [
            SourceSeries(
                source_id=f"{self.base_url}/series/{item.get('id')}",
                title=str(item.get("name") or ""),
                source_name=self.name,
            )
            for item in values
            if item.get("id") and item.get("name")
        ][:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"} or (kind == "latest" and not self.supports_latest):
            return []
        path = "/ranking/manga" if kind == "popular" else (
            self.latest_path.format(page=page) if self.latest_path else f"/category/manga/day/1/{page}"
        )
        response = await self._request("GET", urljoin(f"{self.base_url}/", path), headers={"rsc": "1"} if kind == "popular" else {})
        response.raise_for_status()
        root = _parse_html(response.text)
        result: list[SourceSeries] = []
        for item in root.descendants():
            if not item.has_class("series-list-item"):
                continue
            anchor = _first(item, lambda node: node.tag == "a" and bool(node.attrs.get("href")))
            title = _first(item, lambda node: node.has_class("series-list-item-h"))
            if anchor and title:
                result.append(SourceSeries(urljoin(self.base_url, anchor.attrs["href"]), title.text(), self.name))
        if result:
            return result
        seen: set[str] = set()
        for found in re.finditer(r"""["'](?:hash|id)["']\s*:\s*["']([^"']+)["'][\s\S]{0,500}?["']alt["']\s*:\s*["']([^"']+)""", response.text):
            source_id = f"{self.base_url}/series/{found.group(1)}"
            if source_id not in seen:
                seen.add(source_id)
                result.append(SourceSeries(source_id, found.group(2), self.name))
        return result

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        series_hash = urlparse(urljoin(self.base_url, series_id)).path.rstrip("/").rsplit("/", 1)[-1]
        response = await self._request(
            "GET",
            f"{self.api_url or self.base_url + '/api'}/episodes",
            params={"seriesHash": series_hash, "episodeFrom": "1", "episodeTo": "9999"},
        )
        response.raise_for_status()
        episodes = response.json().get("series", {}).get("episodes", [])
        return [
            SourceChapter(
                source_id=f"{self.base_url}/episodes/{item.get('id')}",
                title=str(item.get("title") or "Capítulo"),
                series_id=series_id,
                source_name=self.name,
                number=self._chapter_number(str(item.get("title") or "")),
            )
            for item in reversed(episodes)
            if item.get("id")
        ]

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        episode_id = urlparse(urljoin(self.base_url, chapter_id)).path.rstrip("/").rsplit("/", 1)[-1]
        response = await self._request("GET", f"{self.api_url or self.base_url + '/api'}/episodes/{episode_id}")
        response.raise_for_status()
        episode = response.json().get("episode", {})
        viewer = next((item.get("viewerId") for item in episode.get("content", []) if item.get("type") == "viewer"), "")
        if not viewer:
            return []
        response = await self._request(
            "GET",
            f"{self.api_url or self.base_url + '/api'}/book/contentsInfo",
            params={
                "comici-viewer-id": viewer,
                "page-from": "0",
                "page-to": "9999",
                "contentId": str(episode.get("contentId", "")),
            },
        )
        response.raise_for_status()
        return [
            SourcePage(
                source_id=f"{item.get('imageUrl')}#scramble={item.get('scramble', '[]')}",
                chapter_id=chapter_id,
                index=int(item.get("sort", index)),
                filename=f"{index}.jpg",
                source_name=self.name,
            )
            for index, item in enumerate(response.json().get("result", []), 1)
            if item.get("imageUrl")
        ]

    async def page_bytes(self, page: SourcePage | str) -> SourcePageContent:
        value = page.source_id if isinstance(page, SourcePage) else page
        parsed = urlparse(value)
        response = await self._request("GET", urlunparse(parsed._replace(fragment="")))
        response.raise_for_status()
        content = response.content
        if parsed.fragment.startswith("scramble="):
            tiles = json.loads(parsed.fragment.removeprefix("scramble="))
            source = Image.open(io.BytesIO(content)).convert("RGB")
            output = Image.new("RGB", source.size)
            width, height = source.width // 4, source.height // 4
            for destination, origin in enumerate(tiles):
                dx, dy = destination // 4 * width, destination % 4 * height
                sx, sy = int(origin) // 4 * width, int(origin) % 4 * height
                output.paste(source.crop((sx, sy, sx + width, sy + height)), (dx, dy))
            if width * 4 < source.width:
                output.paste(source.crop((width * 4, 0, source.width, source.height)), (width * 4, 0))
            if height * 4 < source.height:
                output.paste(source.crop((0, height * 4, width * 4, source.height)), (0, height * 4))
            buffer = io.BytesIO()
            output.save(buffer, "JPEG", quality=90)
            content = buffer.getvalue()
        return SourcePageContent(media_type="image/jpeg", chunks=iter([content]))

    @staticmethod
    def _chapter_number(title: str) -> float | None:
        found = re.search(r"\d+(?:\.\d+)?", title)
        return float(found.group()) if found else None
