"""Implementación común de la API PizzaReader para Nyanko Source v4."""

from urllib.parse import quote, urljoin

try:
    from .madara import (
        MadaraSource,
        SourceChapter,
        SourcePage,
        SourceSeries,
    )
except ImportError:
    pass


class PizzaReaderSource(MadaraSource):
    api_path = "/api"

    @property
    def api_url(self) -> str:
        return f"{self.base_url}{self.api_path}"

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        response = await self._request(
            "GET",
            f"{self.api_url}/search/{quote(query.strip(), safe='')}",
        )
        response.raise_for_status()
        return self._series_from_json(response.json())[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"} or page != 1:
            return []
        response = await self._request("GET", f"{self.api_url}/comics")
        response.raise_for_status()
        comics = response.json().get("comics", [])
        if kind == "latest":
            comics = sorted(
                (comic for comic in comics if comic.get("last_chapter")),
                key=lambda comic: comic["last_chapter"].get("published_on", ""),
                reverse=True,
            )[:10]
        return self._series_from_json({"comics": comics})

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        response = await self._request("GET", f"{self.api_url}{series_id}")
        response.raise_for_status()
        comic = response.json().get("comic") or {}
        result: list[SourceChapter] = []
        for chapter in comic.get("chapters", []):
            number = chapter.get("chapter")
            subchapter = chapter.get("subchapter")
            if number is not None and subchapter is not None:
                number = float(f"{number}.{subchapter}")
            elif number is not None:
                number = float(number)
            teams = [
                team.get("name", "")
                for team in chapter.get("teams", [])
                if isinstance(team, dict)
            ]
            result.append(
                SourceChapter(
                    source_id=chapter.get("url", ""),
                    title=chapter.get("full_title") or "Capítulo",
                    series_id=series_id,
                    source_name=self.name,
                    number=number,
                    scanlator=" & ".join(team for team in teams if team),
                    uploaded_at=chapter.get("published_on") or None,
                )
            )
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        response = await self._request("GET", f"{self.api_url}{chapter_id}")
        response.raise_for_status()
        raw = (response.json().get("chapter") or {}).get("pages", [])
        urls = [urljoin(f"{self.base_url}/", str(url)) for url in raw]
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

    def _series_from_json(self, payload: dict) -> list[SourceSeries]:
        return [
            SourceSeries(
                source_id=comic.get("url", ""),
                title=comic.get("title") or "Sin título",
                source_name=self.name,
            )
            for comic in payload.get("comics", [])
            if comic.get("url")
        ]
