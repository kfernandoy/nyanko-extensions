import json
import re
from html import unescape

try:
    from .base import FuenteBaseSource, SourceChapter, SourcePage, SourceSeries
except ImportError:
    pass

_SNAPSHOT = re.compile(r'wire:snapshot="(.*?)" wire:effects=', re.S)
_RUTA_WIRE = re.compile(r'"(https?://[^"]+/livewire-[a-z0-9]+)/update"')
_CSRF = re.compile(r'csrf-token"\s+content="([^"]*)"')
_TARJETA = re.compile(r'href="[^"]*?/serie/(\d+)"', re.S)
_IMG_ALT = re.compile(r'<img\s[^>]*?src="([^"]+)"[^>]*?alt="([^"]*)"', re.S)
_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S)
_CAPITULO = re.compile(r'href="[^"]*?/chapter/(\d+)"')
_PAGINA = re.compile(r'<img[^>]+class="chapter-img[^"]*"[^>]+src="([^"]+)"')
_TITULO_OG = re.compile(r'<meta property="og:title" content="([^"]*)"')
_IMAGEN_OG = re.compile(r'<meta property="og:image" content="([^"]*)"')
_DESC = re.compile(r'<meta name="description" content="([^"]*)"')
_ETIQUETA = re.compile(r"<[^>]+>")


class FuenteBaseSource:
    pass


class AkayaSource(FuenteBaseSource):
    async def _html(self, path: str) -> str:
        response = await self._request("GET", f"{self.base_url}{path}")
        response.raise_for_status()
        return response.text

    @staticmethod
    def _componente(html: str, nombre: str) -> str | None:
        """Devuelve el snapshot crudo del componente Livewire pedido."""
        for crudo in _SNAPSHOT.findall(html):
            texto = unescape(crudo)
            try:
                if (json.loads(texto).get("memo") or {}).get("name") == nombre:
                    return texto
            except ValueError:
                continue
        return None

    def _series_del_html(self, html: str) -> list[SourceSeries]:
        vistas: dict[str, SourceSeries] = {}
        for enlace in _TARJETA.finditer(html):
            series_id = enlace.group(1)
            if series_id in vistas:
                continue
            # Se retrocede hasta la portada mas cercana; su `alt` ya trae el titulo, y
            # sirve de respaldo el ultimo <h1> del bloque.
            previo = html[max(0, enlace.start() - 20000) : enlace.start()]
            portadas = _IMG_ALT.findall(previo)
            titulos = _H1.findall(previo)
            portada = alt = ""
            if portadas:
                portada, alt = portadas[-1]
            titulo = unescape(alt).strip()
            if not titulo and titulos:
                titulo = unescape(_ETIQUETA.sub("", titulos[-1])).strip()
            if not titulo or not portada:
                continue
            vistas[series_id] = SourceSeries(
                source_id=series_id,
                title=re.sub(r"\s+", " ", titulo)[:120],
                source_name=self.name,
                cover_url=portada,
                web_url=f"{self.base_url}/serie/{series_id}",
            )
        return list(vistas.values())

    async def _pagina_explorador(self, page: int) -> list[SourceSeries]:
        html = await self._html("/explorer/all")
        if page <= 1:
            return self._series_del_html(html)
        snapshot = self._componente(html, "explorer.all")
        ruta = _RUTA_WIRE.search(html)
        token = _CSRF.search(html)
        if not (snapshot and ruta):
            return []
        cuerpo = {
            "_token": token.group(1) if token else "",
            "components": [{
                "snapshot": snapshot,
                "updates": {},
                "calls": [{"path": "", "method": "gotoPage", "params": [page, "page"]}],
            }],
        }
        response = await self._request(
            "POST", f"{ruta.group(1)}/update", json=cuerpo,
            headers={"X-Livewire": "", "Referer": f"{self.base_url}/explorer/all"},
        )
        response.raise_for_status()
        try:
            datos = response.json()
        except ValueError:
            return []
        componentes = datos.get("components") or []
        efectos = (componentes[0].get("effects") or {}) if componentes else {}
        return self._series_del_html(str(efectos.get("html") or ""))

    async def browse(self, kind: str, page: int = 1):
        if kind not in {"popular", "latest"}:
            return {"items": [], "has_more": False}
        items = await self._pagina_explorador(max(page, 1))
        return {"items": items, "has_more": bool(items)}

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        consulta = query.strip().casefold()
        items = await self._pagina_explorador(max(page, 1))
        if consulta:
            # El buscador del sitio es un componente Livewire con debounce; filtrar la
            # pagina del explorador da un resultado equivalente sin depender de el.
            items = [serie for serie in items if consulta in serie.title.casefold()]
        return {"items": items, "has_more": False}

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        html = await self._html(f"/serie/{series_id}")
        titulo = _TITULO_OG.search(html)
        portada = _IMAGEN_OG.search(html)
        descripcion = _DESC.search(html)
        limpio = unescape(titulo.group(1)) if titulo else series_id
        return SourceSeries(
            source_id=series_id,
            title=limpio.removesuffix(" | Akaya.io").strip() or series_id,
            source_name=self.name,
            cover_url=portada.group(1) if portada else None,
            description=unescape(descripcion.group(1)).strip() if descripcion else None,
            web_url=f"{self.base_url}/serie/{series_id}",
        )

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        html = await self._html(f"/serie/{series_id}")
        ids = list(dict.fromkeys(_CAPITULO.findall(html)))
        total = len(ids)
        return [
            SourceChapter(
                source_id=capitulo_id,
                title=f"Capítulo {total - indice}",
                series_id=series_id,
                source_name=self.name,
                number=float(total - indice),
                language=self.language,
            )
            for indice, capitulo_id in enumerate(ids)
        ]

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else str(chapter)
        html = await self._html(f"/chapter/{chapter_id}")
        urls = list(dict.fromkeys(_PAGINA.findall(html)))
        return [
            SourcePage(
                source_id=url,
                chapter_id=chapter_id,
                index=indice,
                filename=url.rsplit("/", 1)[-1] or f"{indice}.webp",
                source_name=self.name,
            )
            for indice, url in enumerate(urls)
        ]



SOURCE = AkayaSource
