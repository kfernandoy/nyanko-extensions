try:
    from .base import (
        FuenteBaseSource, _Node, _TreeParser
    )
except ImportError:
    pass

from html import unescape

class FuenteBaseSource:
    pass


_TARJETA = re.compile(
    r'<a\s+href="([^"]*?/comics/([^"/]+))"[^>]*class="[^"]*falco-card[^"]*"(.*?)</a>',
    re.S,
)
_PORTADA = re.compile(r'<img[^>]+src="([^"]+)"')
_NOMBRE = re.compile(r"<h4[^>]*>(.*?)</h4>", re.S)
_CAPITULO = re.compile(r'href="[^"]*?/comics/[^"/]+/([^"/]+)"')
_LIENZO = re.compile(r'<canvas[^>]+data-src="([^"]+)"[^>]*data-token="([^"]*)"')
_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S)
_DESC = re.compile(r'<meta name="description" content="([^"]*)"')
_ETIQUETA = re.compile(r"<[^>]+>")
_NUMERO = re.compile(r"(\d+(?:\.\d+)?)")


class TenkaiscanSource(FuenteBaseSource):
    async def _html(self, path: str) -> str:
        response = await self._request("GET", f"{self.base_url}{path}")
        response.raise_for_status()
        return response.text

    def _series_del_html(self, html: str) -> list[SourceSeries]:
        vistas: dict[str, SourceSeries] = {}
        for enlace, slug, resto in _TARJETA.findall(html):
            if slug in vistas:
                continue
            portada = _PORTADA.search(resto)
            nombre = _NOMBRE.search(resto)
            titulo = unescape(_ETIQUETA.sub("", nombre.group(1))).strip() if nombre else slug
            vistas[slug] = SourceSeries(
                source_id=slug,
                title=re.sub(r"\s+", " ", titulo) or slug,
                source_name=self.name,
                cover_url=self._absoluta(portada.group(1)) if portada else None,
                web_url=enlace if enlace.startswith("http") else f"{self.base_url}{enlace}",
            )
        return list(vistas.values())

    def _absoluta(self, url: str) -> str:
        if url.startswith("http"):
            return url
        return f"{self.base_url}/{url.lstrip('/')}"

    async def browse(self, kind: str, page: int = 1):
        if kind not in {"popular", "latest"} or page > 1:
            # /comics es un listado unico sin paginacion.
            return {"items": [], "has_more": False}
        return {"items": self._series_del_html(await self._html("/comics")), "has_more": False}

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        if page > 1:
            return {"items": [], "has_more": False}
        consulta = query.strip().casefold()
        items = self._series_del_html(await self._html("/comics"))
        if consulta:
            items = [serie for serie in items if consulta in serie.title.casefold()]
        return {"items": items, "has_more": False}

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        slug = series.source_id if isinstance(series, SourceSeries) else str(series)
        html = await self._html(f"/comics/{slug}")
        titulo = _H1.search(html)
        descripcion = _DESC.search(html)
        return SourceSeries(
            source_id=slug,
            title=unescape(_ETIQUETA.sub("", titulo.group(1))).strip() if titulo else slug,
            source_name=self.name,
            cover_url=f"{self.base_url}/projects/{slug}/{slug}.jpg",
            description=unescape(descripcion.group(1)).strip() if descripcion else None,
            web_url=f"{self.base_url}/comics/{slug}",
        )

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        slug = series.source_id if isinstance(series, SourceSeries) else str(series)
        html = await self._html(f"/comics/{slug}")
        capitulos: list[SourceChapter] = []
        for indice, nombre in enumerate(dict.fromkeys(_CAPITULO.findall(html))):
            numero = _NUMERO.search(nombre)
            capitulos.append(
                SourceChapter(
                    source_id=f"{slug}/{nombre}",
                    title=nombre.replace("-", " ").capitalize(),
                    series_id=slug,
                    source_name=self.name,
                    number=float(numero.group(1)) if numero else float(indice + 1),
                    language=self.language,
                )
            )
        capitulos.sort(key=lambda capitulo: capitulo.number, reverse=True)
        return capitulos

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else str(chapter)
        html = await self._html(f"/comics/{chapter_id}")
        paginas: list[SourcePage] = []
        for indice, (url, token) in enumerate(_LIENZO.findall(html)):
            paginas.append(
                SourcePage(
                    # El token viaja en el id para que page_content pueda reenviarlo.
                    source_id=f"{url}|{token}",
                    chapter_id=chapter_id,
                    index=indice,
                    filename=f"{indice + 1:03d}.jpg",
                    source_name=self.name,
                )
            )
        return paginas

    async def page_bytes(self, page: SourcePage | str) -> SourcePageContent:
        crudo = page.source_id if isinstance(page, SourcePage) else str(page)
        url, _, token = crudo.partition("|")
        capitulo = page.chapter_id if isinstance(page, SourcePage) else ""
        response = await self._request(
            "GET",
            url,
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "X-CSRF-TOKEN": token,
                "Referer": f"{self.base_url}/comics/{capitulo}",
            },
        )
        response.raise_for_status()
        return SourcePageContent(
            media_type=response.headers.get("Content-Type", "image/jpeg"),
            chunks=iter([response.content]),
        )



SOURCE = TenkaiscanSource
