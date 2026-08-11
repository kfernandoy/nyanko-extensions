"""Implementación JSON común de GreenShit."""

from urllib.parse import urljoin

try:
    from .base import FuenteBaseSource, SourceChapter, SourcePage, SourceSeries
except ImportError:
    pass


class GreenShitSource(FuenteBaseSource):
    api_url = ""
    cdn_url = ""
    scan_id = "1"
    default_genre_id = "1"
    requests_per_minute = 120

    def __init__(self, fetcher=None) -> None:
        super().__init__(fetcher)
        self.capabilities.headers.update(
            {"Origin": self.base_url, "scan-id": self.scan_id}
        )

    @staticmethod
    def _rows(data: dict) -> list[dict]:
        return data.get("obras", []) if isinstance(data, dict) else []

    def _series(self, rows: list[dict]) -> list[SourceSeries]:
        return [
            SourceSeries(
                source_id=f"{self.base_url}/obra/{row['obr_id']}",
                title=row["obr_nome"],
                source_name=self.name,
            )
            for row in rows
            if row.get("obr_id") is not None and row.get("obr_nome")
        ]

    async def _catalog(self, endpoint: str, page: int, **params) -> list[SourceSeries]:
        response = await self._request(
            "GET",
            f"{self.api_url}/obras/{endpoint}",
            params={
                "limite": "26",
                "pagina": str(max(page, 1)),
                "gen_id": self.default_genre_id,
                **params,
            },
        )
        response.raise_for_status()
        return self._series(self._rows(response.json()))

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        return (await self._catalog("buscar", 1, obr_nome=query.strip()))[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind == "popular":
            return await self._catalog("ranking", page, tipo="visualizacoes_geral")
        if kind == "latest":
            return await self._catalog("atualizacoes", page)
        return []

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        manga_id = series_id.rstrip("/").rsplit("/", 1)[-1]
        response = await self._request("GET", f"{self.api_url}/obras/{manga_id}")
        response.raise_for_status()
        rows = response.json().get("capitulos", [])
        return [
            SourceChapter(
                source_id=f"{self.base_url}/capitulo/{row['cap_id']}",
                title=("🔒 " if row.get("cap_liberado") is False else "") + row["cap_nome"],
                series_id=series_id,
                source_name=self.name,
                number=float(row["cap_numero"]) if row.get("cap_numero") is not None else None,
                uploaded_at=row.get("cap_criado_em"),
            )
            for row in rows
            if row.get("cap_id") is not None and row.get("cap_nome")
        ]

    def _image(self, src: str, path: str, mime: str | None = None) -> str:
        if src.startswith("http"):
            return src
        src = src.lstrip("/")
        if mime or src.startswith(("uploads/", "wp-content/", "manga_", "WP-manga")):
            if src.startswith("manga_"):
                src = f"wp-content/uploads/WP-manga/data/{src}"
            elif src.startswith("WP-manga"):
                src = f"wp-content/uploads/{src}"
            elif src.startswith("uploads/"):
                src = f"wp-content/{src}"
            return urljoin(f"{self.cdn_url}/", src)
        return urljoin(f"{self.cdn_url}/", f"{path.strip('/')}/{src}")

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        api_id = chapter_id.rstrip("/").rsplit("/", 1)[-1]
        response = await self._request("GET", f"{self.api_url}/capitulos/{api_id}")
        response.raise_for_status()
        data = response.json()
        manga = data.get("obra") or {}
        number = str(data.get("cap_numero") or 0).removesuffix(".0")
        path = f"scans/{manga.get('scan_id', 0)}/obras/{manga.get('obr_id', 0)}/capitulos/{number}"
        return [
            SourcePage(
                source_id=self._image(row["src"], path, row.get("mime")),
                chapter_id=chapter_id,
                index=index,
                filename=row["src"].rsplit("/", 1)[-1],
                source_name=self.name,
            )
            for index, row in enumerate(data.get("cap_paginas", []), 1)
            if row.get("src")
        ]
