try:
    from .base import (
        FuenteBaseSource, _Node, _TreeParser
    )
except ImportError:
    pass

class FuenteBaseSource:
    pass


class ManhwawebSource(FuenteBaseSource):
    """El sitio solo pinta la respuesta del backend, asi que se consume la API."""

    api_url = "https://manhwawebbackend-production.up.railway.app"

    def get_filters(self) -> list[SourceFilter]:
        return [
            SourceFilter("tipo", "Tipo", "select", [
                ("", "Ver todo"), ("manhwa", "Manhwa"), ("manga", "Manga"), ("manhua", "Manhua"),
            ], ""),
            SourceFilter("demografia", "Demografía", "select", [
                ("", "Ver todo"), ("seinen", "Seinen"), ("shonen", "Shonen"),
                ("josei", "Josei"), ("shojo", "Shojo"),
            ], ""),
            SourceFilter("estado", "Estado", "select", [
                ("", "Ver todo"), ("publicandose", "Publicándose"), ("finalizado", "Finalizado"),
            ], ""),
            SourceFilter("erotico", "Erótico", "select", [
                ("", "Ver todo"), ("si", "Sí"), ("no", "No"),
            ], ""),
            SourceFilter("generes", "Géneros", "multi_select", list(_MANHWAWEB_GENRES), []),
            SourceFilter("order_item", "Ordenar por", "select", [
                ("alfabetico", "Alfabético"), ("creacion", "Creación"), ("num_chapter", "Num. Capítulos"),
            ], "alfabetico"),
            SourceFilter("order_dir", "Dirección", "select", [
                ("desc", "Descendente"), ("asc", "Ascendente"),
            ], "desc"),
        ]

    async def browse(self, kind: str, page: int = 1):
        # El top de /manhwa/nuevos llega completo en una sola respuesta: son 17 series y
        # no hay pagina 2, asi que la biblioteca se cortaba ahi. Se conserva como cabecera
        # y a partir de la primera pagina se sigue por la busqueda, que si pagina.
        if kind == "popular":
            destacados = []
            if page == 1:
                payload = await self._api("/manhwa/nuevos")
                block = payload.get("top") or {}
                items = list(block.get("manhwas_esp") or []) + list(block.get("manhwas_raw") or [])
                items = self._distinct(items, "link")
                items.sort(key=lambda item: item.get("numero") or 0, reverse=True)
                destacados = [self._popular_series(item) for item in items]

            catalogo = await self.search("", page, {})
            if isinstance(catalogo, dict):
                series, hay_mas = catalogo.get("items", []), catalogo.get("has_more", False)
            else:
                series, hay_mas = getattr(catalogo, "items", []), getattr(catalogo, "has_more", False)

            vistos = {serie.source_id for serie in destacados}
            return {
                "items": destacados + [s for s in series if s.source_id not in vistos],
                "has_more": hay_mas,
            }
        if kind == "latest":
            payload = await self._api("/latest/new-manhwa")
            block = payload.get("manhwas") or {}
            items = (
                list(block.get("manhwas_esp") or [])
                + list(block.get("manhwas_raw") or [])
                + list(block.get("_manhwas") or [])
            )
            items = self._distinct(items, "id_rel")
            items.sort(key=lambda item: item.get("create") or 0, reverse=True)
            return {"items": [self._latest_series(item) for item in items], "has_more": False}
        return {"items": [], "has_more": False}

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        values = filters or {}
        genres = values.get("generes")
        direction = str(values.get("order_dir") or "desc")
        params = [("buscar", query.strip())]
        params.extend(
            (key, str(values.get(key) or ""))
            for key in ("tipo", "demografia", "estado", "erotico")
        )
        params.append((
            "generes",
            "a".join(str(genre) for genre in genres) if isinstance(genres, list) else "",
        ))
        params.append(("order_dir", "asc" if direction == "asc" else "desc"))
        params.append(("order_item", str(values.get("order_item") or "alfabetico")))
        # La API cuenta las paginas desde cero.
        params.append(("page", str(max(page - 1, 0))))
        payload = await self._api("/manhwa/library", params)
        return {
            "items": [self._search_series(item) for item in payload.get("data") or []],
            "has_more": bool(payload.get("next")),
        }

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        payload = await self._api(f"/manhwa/see/{self._slug(series_id)}")
        description = str(payload.get("_sinopsis") or "")
        alternative = str(payload.get("_name") or "").strip()
        if alternative:
            description = f"{description}\n\n" if description else description
            description += f"Nombres alternativos: {alternative}"
        extras = payload.get("_extras") or {}
        authors = [str(author) for author in extras.get("autores") or []]
        return SourceSeries(
            source_id=series_id,
            title=str(payload.get("name_esp") or ""),
            source_name=self.name,
            cover_url=payload.get("_imagen") or None,
            description=description or None,
            author=", ".join(authors) or None,
            status=self._status(str(payload.get("_status") or "")),
            content_tags=tuple(
                str(next(iter(entry.values())))
                for entry in payload.get("_categoris") or []
                if isinstance(entry, dict) and entry
            ),
            web_url=urljoin(f"{self.base_url}/", series_id),
        )

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        payload = await self._api(f"/manhwa/see/{self._slug(series_id)}")
        identifier = str(payload.get("_id") or "")
        real_id = str(payload.get("real_id") or "")
        result: list[SourceChapter] = []
        for item in payload.get("chapters") or []:
            url = item.get("link") or item.get("link_raw")
            # Sin fecha o sin ninguno de los dos enlaces el capitulo no es legible.
            if item.get("create") is None or not url:
                continue
            if identifier and real_id:
                url = str(url).replace(identifier, real_id)
            number = float(item.get("chapter") or 0)
            result.append(
                SourceChapter(
                    source_id=urlparse(str(url)).path.lstrip("/") or str(url),
                    title=f"Capítulo {self._chapter_number(number)}",
                    series_id=series_id,
                    source_name=self.name,
                    number=number,
                    scanlator="Esp" if item.get("link") else "Raw",
                    language=self.language,
                    uploaded_at=self._epoch_millis(item.get("create")),
                )
            )
        result.sort(key=lambda chapter: chapter.number or 0.0, reverse=True)
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else str(chapter)
        payload = await self._api(f"/chapters/see/{self._slug(chapter_id)}")
        urls = [
            str(image)
            for image in (payload.get("chapter") or {}).get("img") or []
            if str(image).startswith("http")
        ]
        return [
            SourcePage(
                source_id=url,
                chapter_id=chapter_id,
                index=index,
                filename=urlparse(url).path.rsplit("/", 1)[-1] or f"{index}.jpg",
                source_name=self.name,
            )
            for index, url in enumerate(urls)
        ]

    async def _api(self, path: str, params: Any = None) -> Any:
        response = await self._request("GET", f"{self.api_url}{path}", params=params or {})
        response.raise_for_status()
        return response.json()

    def _popular_series(self, item: dict) -> SourceSeries:
        slug = str(item.get("link") or "")
        slug = (slug[1:] if slug.startswith("/") else slug).replace("manga/", "manhwa/")
        return self._series_entry(slug, item.get("name"), item.get("imagen"))

    def _latest_series(self, item: dict) -> SourceSeries:
        return self._series_entry(
            f"manhwa/{item.get('id_rel') or ''}", item.get("name_manhwa"), item.get("img"),
        )

    def _search_series(self, item: dict) -> SourceSeries:
        return self._series_entry(
            f"manhwa/{item.get('real_id') or ''}", item.get("the_real_name"), item.get("_imagen"),
        )

    def _series_entry(self, slug: str, title: Any, cover: Any) -> SourceSeries:
        return SourceSeries(
            source_id=slug,
            title=str(title or ""),
            source_name=self.name,
            cover_url=str(cover) if cover else None,
            web_url=urljoin(f"{self.base_url}/", slug),
        )

    @staticmethod
    def _distinct(items: list, key: str) -> list:
        return list({str(item.get(key)): item for item in reversed(items)}.values())[::-1]

    @staticmethod
    def _slug(value: str) -> str:
        return value.rstrip("/").rsplit("/", 1)[-1]

    @staticmethod
    def _status(value: str) -> str | None:
        return {"publicandose": "ongoing", "finalizado": "completed"}.get(value.strip())

    @staticmethod
    def _chapter_number(value: float) -> str:
        text = str(value)
        return text[:-2] if text.endswith(".0") else text

    @staticmethod
    def _epoch_millis(value: Any) -> str | None:
        from datetime import datetime, timezone

        try:
            moment = datetime.fromtimestamp(int(value) / 1000, timezone.utc)
        except (TypeError, ValueError, OSError, OverflowError):
            return None
        return moment.replace(tzinfo=None).isoformat()




SOURCE = ManhwawebSource
