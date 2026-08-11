"""Implementación HTML común de EroMuse."""

from urllib.parse import urljoin

try:
    from .base import FuenteBaseSource, SourceChapter, SourcePage, SourceSeries, _image_url, _parse_html
except ImportError:
    pass


class EroMuseSource(FuenteBaseSource):
    profile = "eightmuses"

    def _tiles(self, html: str, base: str) -> list[tuple[str, str, str]]:
        root = _parse_html(html)
        tile_class = "a-click" if self.profile == "erofus" else "c-tile"
        result: list[tuple[str, str, str]] = []
        for anchor in (node for node in root.descendants("a") if node.has_class(tile_class)):
            image = next(iter(anchor.descendants("img")), None)
            href = anchor.attrs.get("href")
            if href and image is not None:
                result.append((urljoin(base, href), anchor.text(), _image_url(image, base)))
        return result

    async def _catalog(self, url: str) -> list[SourceSeries]:
        response = await self._request("GET", url)
        response.raise_for_status()
        return [
            SourceSeries(source_id=href, title=title or href.rstrip("/").rsplit("/", 1)[-1], source_name=self.name)
            for href, title, _image in self._tiles(response.text, url)
            if "members-only" not in href
        ]

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        params = {"search": query.strip()} if self.profile == "erofus" else {"q": query.strip()}
        endpoint = self.base_url if self.profile == "erofus" else f"{self.base_url}/search"
        response = await self._request("GET", endpoint, params=params)
        response.raise_for_status()
        return [
            SourceSeries(source_id=href, title=title or href.rstrip("/").rsplit("/", 1)[-1], source_name=self.name)
            for href, title, _image in self._tiles(response.text, endpoint)
        ][:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"}:
            return []
        if self.profile == "erofus":
            url = f"{self.base_url}/comics/various-authors"
            params = {"sort": "viewed" if kind == "popular" else "recent", "page": page}
        else:
            url = f"{self.base_url}/comics/album/Various-Authors"
            params = {"sort": "date"} if kind == "latest" else {}
            if page > 1:
                params["page"] = page
        response = await self._request("GET", url, params=params)
        response.raise_for_status()
        return [
            SourceSeries(source_id=href, title=title or href.rstrip("/").rsplit("/", 1)[-1], source_name=self.name)
            for href, title, _image in self._tiles(response.text, url)
        ]

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        response = await self._request("GET", series_id)
        response.raise_for_status()
        tiles = self._tiles(response.text, series_id)
        picture_part = "/pic/" if self.profile == "erofus" else "/comics/picture/"
        chapter_part = "/comics/" if self.profile == "erofus" else "/comics/album/"
        result = [
            SourceChapter(
                source_id=href,
                title=title or href.rstrip("/").rsplit("/", 1)[-1],
                series_id=series_id,
                source_name=self.name,
            )
            for href, title, _image in tiles
            if chapter_part in href and picture_part not in href
        ]
        if any(picture_part in href for href, _title, _image in tiles):
            result.append(
                SourceChapter(
                    source_id=series_id,
                    title="Chapter",
                    series_id=series_id,
                    source_name=self.name,
                )
            )
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        pending, visited, urls = [chapter_id], set(), []
        picture_part = "/pic/" if self.profile == "erofus" else "/comics/picture/"
        old, new = ("/thumb/", "/medium/") if self.profile == "erofus" else ("/th/", "/fl/")
        while pending:
            url = pending.pop()
            if url in visited:
                continue
            visited.add(url)
            response = await self._request("GET", url)
            response.raise_for_status()
            for href, _title, image in self._tiles(response.text, url):
                if picture_part in href:
                    urls.append(image.replace(old, new))
                elif href not in visited:
                    pending.append(href)
        return [
            SourcePage(
                source_id=url,
                chapter_id=chapter_id,
                index=index,
                filename=url.rsplit("/", 1)[-1].split("?", 1)[0],
                source_name=self.name,
            )
            for index, url in enumerate(dict.fromkeys(urls), 1)
        ]
