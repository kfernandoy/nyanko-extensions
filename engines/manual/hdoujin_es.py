try:
    from .madara import (
        MadaraSource, _Node, _TreeParser
    )
except ImportError:
    pass

class MadaraSource:
    pass


class HdoujinSource(MadaraSource):
    # Mascara del idioma que sirve esta variante; 0 = sin filtro (todos).
    language_mask = 0

    def _cabeceras(self) -> dict[str, str]:
        return {"Referer": f"{self.base_url}/", "Origin": self.base_url}

    async def _pedir(self, path: str, params: dict | None = None) -> dict:
        response = await self._request(
            "GET", f"{_API}{path}", params=params or {}, headers=self._cabeceras(),
        )
        response.raise_for_status()
        try:
            return response.json() or {}
        except ValueError:
            return {}

    def _serie(self, fila: dict) -> SourceSeries | None:
        if not (fila.get("id") and fila.get("key")):
            return None
        miniatura = fila.get("thumbnail") or {}
        portada = miniatura.get("path") if isinstance(miniatura, dict) else None
        return SourceSeries(
            source_id=f"{fila['id']}/{fila['key']}",
            title=str(fila.get("title") or "").strip() or str(fila.get("id")),
            source_name=self.name,
            cover_url=portada,
            web_url=f"{self.base_url}/g/{fila['id']}/{fila['key']}",
        )

    def _listado(self, payload: dict) -> dict:
        filas = payload.get("entries") or []
        items = [serie for fila in filas if (serie := self._serie(fila))]
        # `total` es el global; se pagina mientras la pagina venga llena.
        limite = int(payload.get("limit") or 0)
        return {"items": items, "has_more": bool(limite) and len(filas) >= limite}

    def _parametros(self, page: int) -> dict:
        params: dict[str, object] = {"page": max(page, 1)}
        if self.language_mask:
            params["lang"] = self.language_mask
        return params

    async def browse(self, kind: str, page: int = 1):
        if kind not in {"popular", "latest"}:
            return {"items": [], "has_more": False}
        ruta = "/books/popular" if kind == "popular" else "/books"
        return self._listado(await self._pedir(ruta, self._parametros(page)))

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        params = self._parametros(page)
        if query.strip():
            params["s"] = query.strip()
        return self._listado(await self._pedir("/books", params))

    @staticmethod
    def _nombres(tags: object, espacio: str) -> list[str]:
        return [
            str(tag.get("name") or "").strip()
            for tag in (tags if isinstance(tags, list) else [])
            if isinstance(tag, dict)
            and str(tag.get("namespace") or "") == espacio
            and str(tag.get("name") or "").strip()
        ]

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        data = await self._pedir(f"/books/detail/{series_id}")
        miniaturas = data.get("thumbnails") or {}
        base = str(miniaturas.get("base") or "")
        principal = miniaturas.get("main") or {}
        etiquetas = data.get("tags")
        todas = [
            str(tag.get("name") or "").strip()
            for tag in (etiquetas if isinstance(etiquetas, list) else [])
            if isinstance(tag, dict) and str(tag.get("name") or "").strip()
        ]
        return SourceSeries(
            source_id=series_id,
            title=str(data.get("title") or "").strip() or series_id,
            source_name=self.name,
            cover_url=f"{base}{principal.get('path', '')}" if base and principal else None,
            description=str(data.get("subtitle") or "").strip() or None,
            author=", ".join(self._nombres(etiquetas, "artist")) or None,
            artist=", ".join(self._nombres(etiquetas, "artist")) or None,
            content_tags=tuple(dict.fromkeys(todas)),
            web_url=f"{self.base_url}/g/{series_id}",
        )

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        data = await self._pedir(f"/books/detail/{series_id}")
        # Cada libro es una galeria de un solo capitulo.
        return [
            SourceChapter(
                source_id=series_id,
                title=str(data.get("title_short") or data.get("title") or "Galería"),
                series_id=series_id,
                source_name=self.name,
                number=1.0,
                language=self.language,
            )
        ]

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else str(chapter)
        data = await self._pedir(f"/books/detail/{chapter_id}")
        miniaturas = data.get("thumbnails") or {}
        base = str(miniaturas.get("base") or "")
        entradas = miniaturas.get("entries") or []
        paginas: list[SourcePage] = []
        for indice, entrada in enumerate(entradas):
            ruta = str(entrada.get("path") or "") if isinstance(entrada, dict) else ""
            if not ruta:
                continue
            url = f"{base}{ruta}"
            paginas.append(
                SourcePage(
                    source_id=url,
                    chapter_id=chapter_id,
                    index=indice,
                    filename=urlparse(url).path.rsplit("/", 1)[-1] or f"{indice}.webp",
                    source_name=self.name,
                )
            )
        return paginas



SOURCE = HdoujinSource
