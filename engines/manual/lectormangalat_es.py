import re
from html import unescape

try:
    from .base import FuenteBaseSource, SourceChapter, SourcePage, SourceSeries
except ImportError:
    pass

# Solo las tarjetas de la rejilla llevan `card-cover-link`; los carruseles usan otras clases.
_TARJETA = re.compile(
    r'<a\s+href="/comics/([a-z0-9\-]+)"\s+class="card-cover-link"\s+title="([^"]*)"[^>]*>'
    r'\s*<img[^>]+src="([^"]+)"',
    re.S,
)
_CAPITULO = re.compile(r'href="/comics/[a-z0-9\-]+/([a-z0-9\-]*\d[a-z0-9\-.]*)"')
_PAGINA = re.compile(r'<img[^>]+src="(https://media\.[^"]+/capitulos/[^"]+)"')
_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S)
_DESC = re.compile(r'<meta name="description" content="([^"]*)"')
_PORTADA_OG = re.compile(r'<meta property="og:image" content="([^"]*)"')
_ETIQUETA = re.compile(r"<[^>]+>")
_NUMERO = re.compile(r"(\d+(?:\.\d+)?)")


class FuenteBaseSource:
    pass


class LectormangalatSource(FuenteBaseSource):
    async def _html(self, path: str) -> str:
        response = await self._request("GET", f"{self.base_url}{path}")
        response.raise_for_status()
        return response.text

    def _series_del_html(self, html: str) -> list[SourceSeries]:
        vistas: dict[str, SourceSeries] = {}
        for slug, titulo, portada in _TARJETA.findall(html):
            if slug in vistas:
                continue
            vistas[slug] = SourceSeries(
                source_id=slug,
                title=unescape(titulo).strip() or slug,
                source_name=self.name,
                cover_url=portada,
                web_url=f"{self.base_url}/comics/{slug}",
            )
        return list(vistas.values())

    async def browse(self, kind: str, page: int = 1):
        if kind not in {"popular", "latest"}:
            return {"items": [], "has_more": False}
        orden = "likes_count" if kind == "popular" else ""
        ruta = f"/comics?page={max(page, 1)}"
        if orden:
            ruta += f"&order_item={orden}"
        items = self._series_del_html(await self._html(ruta))
        return {"items": items, "has_more": bool(items)}

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        consulta = query.strip()
        ruta = f"/comics?page={max(page, 1)}"
        if consulta:
            # El parametro es `search`. Con `title`, `q` o `name` el sitio responde 200
            # pero ignora el filtro y devuelve el listado completo.
            ruta += f"&search={consulta.replace(' ', '+')}"
        items = self._series_del_html(await self._html(ruta))
        return {"items": items, "has_more": bool(items)}

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        slug = series.source_id if isinstance(series, SourceSeries) else str(series)
        html = await self._html(f"/comics/{slug}")
        titulo = _H1.search(html)
        descripcion = _DESC.search(html)
        portada = _PORTADA_OG.search(html)
        return SourceSeries(
            source_id=slug,
            title=unescape(_ETIQUETA.sub("", titulo.group(1))).strip() if titulo else slug,
            source_name=self.name,
            cover_url=portada.group(1) if portada else None,
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
        urls = list(dict.fromkeys(_PAGINA.findall(html)))
        return [
            SourcePage(
                source_id=url,
                chapter_id=chapter_id,
                index=indice,
                filename=url.rsplit("/", 1)[-1] or f"{indice + 1:03d}.webp",
                source_name=self.name,
            )
            for indice, url in enumerate(urls)
        ]



SOURCE = LectormangalatSource
