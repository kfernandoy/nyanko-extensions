try:
    from .madara import (
        MadaraSource, _Node, _TreeParser
    )
except ImportError:
    pass

class MadaraSource:
    pass


_POR_PAGINA = 24


class TraduccionesmoonlightSource(MadaraSource):
    def __init__(self, fetcher=None) -> None:
        super().__init__(fetcher)
        self._catalogo: list[dict] | None = None

    async def _json(self, path: str) -> dict:
        response = await self._request("GET", f"{self.base_url}{path}")
        response.raise_for_status()
        try:
            return response.json() or {}
        except ValueError:
            return {}

    async def _todo_el_catalogo(self) -> list[dict]:
        if self._catalogo is None:
            datos = await self._json("/api/comics")
            respuesta = datos.get("response")
            self._catalogo = [
                fila for fila in (respuesta or [])
                if isinstance(fila, dict) and fila.get("slug")
            ]
        return self._catalogo

    def _serie(self, fila: dict) -> SourceSeries:
        generos = fila.get("genders")
        etiquetas = [
            str(genero.get("name") or "").strip()
            for genero in (generos or [])
            if isinstance(genero, dict) and str(genero.get("name") or "").strip()
        ]
        estado = fila.get("state")
        return SourceSeries(
            source_id=str(fila.get("slug") or ""),
            title=str(fila.get("name") or "").strip() or str(fila.get("slug") or ""),
            source_name=self.name,
            cover_url=str(fila.get("urlImg") or "") or None,
            description=str(fila.get("sinopsis") or "").strip() or None,
            author=self._primer_nombre(fila.get("autors")),
            artist=self._primer_nombre(fila.get("artists")),
            status=str((estado or {}).get("name") or "").strip() or None
            if isinstance(estado, dict) else None,
            content_tags=tuple(etiquetas),
            web_url=f"{self.base_url}/ver/{fila.get('slug', '')}",
        )

    @staticmethod
    def _primer_nombre(valores: object) -> str | None:
        nombres = [
            str(valor.get("name") or "").strip()
            for valor in (valores if isinstance(valores, list) else [])
            if isinstance(valor, dict) and str(valor.get("name") or "").strip()
        ]
        return ", ".join(nombres) or None

    def _pagina(self, filas: list[dict], page: int) -> dict:
        inicio = (max(page, 1) - 1) * _POR_PAGINA
        trozo = filas[inicio : inicio + _POR_PAGINA]
        return {
            "items": [self._serie(fila) for fila in trozo],
            "has_more": inicio + _POR_PAGINA < len(filas),
        }

    async def browse(self, kind: str, page: int = 1):
        if kind not in {"popular", "latest"}:
            return {"items": [], "has_more": False}
        filas = list(await self._todo_el_catalogo())
        if kind == "latest":
            filas.sort(key=lambda fila: str(fila.get("actualizacionCap") or ""), reverse=True)
        elif kind == "popular":
            filas.sort(key=lambda fila: float(fila.get("averageRating") or 0), reverse=True)
        return self._pagina(filas, page)

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        consulta = query.strip().casefold()
        filas = await self._todo_el_catalogo()
        if consulta:
            filas = [
                fila for fila in filas
                if consulta in str(fila.get("name") or "").casefold()
                or consulta in str(fila.get("alternativeName") or "").casefold()
            ]
        return self._pagina(list(filas), page)

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        slug = series.source_id if isinstance(series, SourceSeries) else str(series)
        datos = await self._json(f"/api/showProject/{slug}")
        fila = datos.get("response")
        if not isinstance(fila, dict) or not fila.get("slug"):
            return series if isinstance(series, SourceSeries) else self._serie({"slug": slug})
        return self._serie(fila)

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        slug = series.source_id if isinstance(series, SourceSeries) else str(series)
        datos = await self._json(f"/api/showProject/{slug}")
        fila = datos.get("response") or {}
        capitulos: list[SourceChapter] = []
        # `lastChapters` trae la lista completa, no solo los ultimos.
        for entrada in fila.get("lastChapters") or []:
            if not isinstance(entrada, dict) or not entrada.get("slug"):
                continue
            try:
                numero = float(entrada.get("num") or 0)
            except (TypeError, ValueError):
                numero = 0.0
            capitulos.append(
                SourceChapter(
                    source_id=f"{slug}/{entrada['slug']}",
                    title=str(entrada.get("name") or f"Capítulo {numero:g}"),
                    series_id=slug,
                    source_name=self.name,
                    number=numero,
                    language=self.language,
                )
            )
        capitulos.sort(key=lambda capitulo: capitulo.number, reverse=True)
        return capitulos

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else str(chapter)
        datos = await self._json(f"/api/showProject/{chapter_id}")
        fila = datos.get("response") or {}
        contenido = fila.get("pages")
        crudo = (contenido or {}).get("urlImg") if isinstance(contenido, dict) else None
        # `urlImg` es un string con un JSON dentro; hay que deserializarlo aparte.
        if isinstance(crudo, str):
            try:
                urls = json.loads(crudo)
            except ValueError:
                urls = []
        else:
            urls = crudo if isinstance(crudo, list) else []
        paginas: list[SourcePage] = []
        for indice, url in enumerate(urls):
            if not url:
                continue
            paginas.append(
                SourcePage(
                    source_id=str(url),
                    chapter_id=chapter_id,
                    index=indice,
                    filename=str(url).rsplit("/", 1)[-1] or f"{indice + 1:03d}.jpg",
                    source_name=self.name,
                )
            )
        return paginas



SOURCE = TraduccionesmoonlightSource
