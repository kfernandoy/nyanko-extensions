try:
    from .madara import (
        MadaraSource, _Node, _TreeParser
    )
except ImportError:
    pass

class MadaraSource:
    pass


class LectormonlineSource(MadaraSource):
    async def _json(self, path: str, params: dict | None = None) -> dict:
        response = await self._request(
            "GET", f"{self.base_url}{path}", params=params or {},
        )
        response.raise_for_status()
        try:
            return response.json() or {}
        except ValueError:
            return {}

    async def _html(self, path: str) -> str:
        response = await self._request("GET", f"{self.base_url}{path}")
        response.raise_for_status()
        return response.text

    @staticmethod
    def _resolver(datos: list, indice, vistos: frozenset = frozenset()):
        """Deshace el aplanado de SvelteKit siguiendo los indices."""
        if not isinstance(indice, int) or not 0 <= indice < len(datos):
            return indice
        if indice in vistos:
            return None
        valor = datos[indice]
        marcados = vistos | {indice}
        if isinstance(valor, dict):
            return {
                clave: GatoLibreriaSource._resolver(datos, hijo, marcados)
                for clave, hijo in valor.items()
            }
        if isinstance(valor, list):
            return [GatoLibreriaSource._resolver(datos, hijo, marcados) for hijo in valor]
        return valor

    async def _payload(self, path: str) -> dict:
        datos = await self._json(f"{path}/__data.json")
        nodos = datos.get("nodes") or []
        for nodo in reversed(nodos):
            if isinstance(nodo, dict) and nodo.get("type") == "data":
                plano = nodo.get("data")
                if isinstance(plano, list) and plano:
                    resuelto = self._resolver(plano, 0)
                    if isinstance(resuelto, dict):
                        return resuelto
        return {}

    @staticmethod
    def _genero(valor) -> str:
        """Nombre legible de un genero.

        La API no devuelve cadenas sino objetos completos:
        ``{"id": 13, "name": "Drama", "slug": "drama", "createdAt": ...}``. Al pasarlos
        por ``str()`` la ficha mostraba el diccionario entero como etiqueta.
        Se acepta la cadena suelta por si alguna respuesta viene ya aplanada.
        """
        if isinstance(valor, dict):
            return str(valor.get("name") or valor.get("slug") or "").strip()
        return str(valor or "").strip()

    def _serie(self, fila: dict) -> SourceSeries:
        generos = fila.get("genres")
        return SourceSeries(
            source_id=str(fila.get("slug") or fila.get("id") or ""),
            title=str(fila.get("title") or "").strip() or str(fila.get("slug") or ""),
            source_name=self.name,
            cover_url=str(fila.get("coverImage") or fila.get("urlCover") or "") or None,
            description=str(fila.get("description") or "").strip() or None,
            author=str(fila.get("author") or "").strip() or None,
            artist=str(fila.get("artist") or "").strip() or None,
            status=str(fila.get("status") or "").strip() or None,
            content_tags=tuple(
                nombre for genero in generos if (nombre := self._genero(genero))
            ) if isinstance(generos, list) else (),
            web_url=f"{self.base_url}/comics/{fila.get('slug', '')}",
        )

    def _listado(self, datos: dict) -> dict:
        filas = [fila for fila in (datos.get("data") or []) if isinstance(fila, dict)]
        paginacion = datos.get("pagination") or {}
        pagina = int(paginacion.get("page") or 1)
        total = int(paginacion.get("totalPages") or 0)
        return {
            "items": [self._serie(fila) for fila in filas],
            "has_more": pagina < total,
        }

    async def browse(self, kind: str, page: int = 1):
        if kind not in {"popular", "latest"}:
            return {"items": [], "has_more": False}
        # La API no admite ordenar: probado `sort=views`, devuelve exactamente la misma
        # secuencia. Se sirve el mismo listado en ambas pestañas en vez de simular un
        # orden que el sitio no aplica.
        params: dict[str, object] = {"page": max(page, 1), "limit": _POR_PAGINA}
        return self._listado(await self._json("/api/comics", params))

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        params: dict[str, object] = {"page": max(page, 1), "limit": _POR_PAGINA}
        if query.strip():
            # Es `search`: con `title` o `q` la API ignora el filtro.
            params["search"] = query.strip()
        return self._listado(await self._json("/api/comics", params))

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        slug = series.source_id if isinstance(series, SourceSeries) else str(series)
        payload = await self._payload(f"/comics/{slug}")
        comic = payload.get("comic")
        if not isinstance(comic, dict) or not comic.get("title"):
            return series if isinstance(series, SourceSeries) else self._serie({"slug": slug})
        comic.setdefault("slug", slug)
        return self._serie(comic)

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        slug = series.source_id if isinstance(series, SourceSeries) else str(series)
        # La API solo expone `recent_chapters` (2 ultimos); la lista completa esta en el
        # HTML de la ficha.
        html = await self._html(f"/comics/{slug}")
        ids = list(dict.fromkeys(_CAPITULO_HTML.findall(html)))
        capitulos: list[SourceChapter] = []
        for indice, chapter_id in enumerate(ids):
            capitulos.append(
                SourceChapter(
                    source_id=f"{slug}/{chapter_id}",
                    title=f"Capítulo {len(ids) - indice}",
                    series_id=slug,
                    source_name=self.name,
                    number=float(len(ids) - indice),
                    language=self.language,
                )
            )
        return capitulos

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else str(chapter)
        slug, _, numero = chapter_id.partition("/")
        payload = await self._payload(f"/comics/{slug}/chapters/{numero}")
        datos = payload.get("chapter")
        urls = (datos or {}).get("url_pages") if isinstance(datos, dict) else None
        paginas: list[SourcePage] = []
        for indice, url in enumerate(urls or []):
            if not url:
                continue
            paginas.append(
                SourcePage(
                    source_id=str(url),
                    chapter_id=chapter_id,
                    index=indice,
                    filename=str(url).rsplit("/", 1)[-1] or f"{indice + 1:03d}.webp",
                    source_name=self.name,
                )
            )
        return paginas



SOURCE = LectormonlineSource
