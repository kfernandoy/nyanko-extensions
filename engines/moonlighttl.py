"""Implementación común de MoonlightTL."""

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


class MoonlightTLSource(MadaraSource):
    profile = "regular"
    requests_per_minute = 120

    @staticmethod
    def _rows(payload) -> list[dict]:
        if isinstance(payload, list):
            return [row for item in payload for row in MoonlightTLSource._rows(item)]
        if not isinstance(payload, dict):
            return []
        if "project" in payload:
            return MoonlightTLSource._rows(payload["project"])
        if payload.get("slug") and payload.get("name"):
            return [payload]
        return [
            row
            for key in ("diario", "semanal", "mensual")
            for row in MoonlightTLSource._rows(payload.get(key, []))
        ]

    def _series(self, payload) -> list[SourceSeries]:
        result: list[SourceSeries] = []
        seen: set[str] = set()
        for row in self._rows(payload):
            slug = str(row.get("slug", "")).strip()
            title = str(row.get("name", "")).strip()
            if slug and title and slug not in seen:
                seen.add(slug)
                result.append(
                    SourceSeries(
                        source_id=f"{self.base_url}/ver/{slug}",
                        title=title,
                        source_name=self.name,
                    )
                )
        return result

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        response = await self._request("GET", f"{self.base_url}/api/comics")
        response.raise_for_status()
        needle = query.strip().casefold()
        rows = self._rows(response.json().get("response", []))
        matches = [
            row
            for row in rows
            if needle in str(row.get("name", "")).casefold()
            or needle in str(row.get("alternativeName", "")).casefold()
        ]
        return self._series(matches)[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"} or page != 1:
            return []
        endpoint = "topSerie" if kind == "popular" else "lastUpdates"
        response = await self._request("GET", f"{self.base_url}/api/{endpoint}")
        response.raise_for_status()
        return self._series(response.json().get("response", []))

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        slug = series_id.rstrip("/").rsplit("/", 1)[-1]
        response = await self._request("GET", f"{self.base_url}/api/showProject/{slug}")
        response.raise_for_status()
        data = response.json().get("response", {})
        result: list[SourceChapter] = []
        for row in data.get("lastChapters", []):
            chapter_slug = str(row.get("slug", "")).strip()
            if not chapter_slug:
                continue
            number = row.get("num")
            title = f"Capítulo {number:g}" if isinstance(number, (int, float)) else "Capítulo"
            if row.get("name"):
                title += f" - {row['name']}"
            result.append(
                SourceChapter(
                    source_id=f"{self.base_url}/ver/{slug}/{chapter_slug}",
                    title=title,
                    series_id=series_id,
                    source_name=self.name,
                    number=float(number) if isinstance(number, (int, float)) else None,
                    uploaded_at=row.get("created_at"),
                )
            )
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        response = await self._request("GET", chapter_id)
        response.raise_for_status()
        root = _parse_html(response.text)
        form = _first(root, lambda node: node.tag == "form" and node.attrs.get("method", "").lower() == "post")
        if form is not None and form.attrs.get("action"):
            data = {
                node.attrs["name"]: node.attrs.get("value", "")
                for node in form.descendants("input")
                if node.attrs.get("name")
            }
            response = await self._request(
                "POST",
                urljoin(str(response.url), form.attrs["action"]),
                data=data,
                headers={"Referer": str(response.url)},
            )
            response.raise_for_status()
            root = _parse_html(response.text)
        urls: list[str] = []
        for image in root.descendants("img"):
            if self.profile == "asteria":
                selected = image.has_class("block")
            else:
                parent = image.parent
                selected = bool(
                    parent
                    and (
                        parent.tag == "main"
                        or any(node.tag == "main" and node.has_class("contenedor") for node in self._parents(image))
                    )
                )
            if selected and (url := _image_url(image, str(response.url))):
                urls.append(url)
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

    @staticmethod
    def _parents(node):
        while node.parent is not None:
            node = node.parent
            yield node
