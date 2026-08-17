"""Implementación HTML común de GalleryAdults."""

import json
import re
from urllib.parse import urljoin, urlparse

try:
    from .base import FuenteBaseSource, SourceChapter, SourcePage, SourceSeries, _first, _image_url, _parse_html
except ImportError:
    pass


class GalleryAdultsSource(FuenteBaseSource):
    manga_language = ""
    profile = ""

    @staticmethod
    def _es_enlace_de_galeria(href: str) -> bool:
        """`True` si el href apunta a la ficha de una obra.

        Los sitios del tema no comparten prefijo: imhentai, hentaiera, hentaizap y
        hentaienvy usan `/gallery/123/`, mientras que asmhentai y nhentai.xxx usan
        `/g/123/`. Filtrar solo por `/gallery/` dejaba a estos dos cayendo al enlace
        de categoria, asi que el listado colapsaba a 2 tarjetas repetidas.
        """
        ruta = urlparse(href).path
        return bool(re.search(r"/(?:gallery|g|view)/\d+", ruta))

    @staticmethod
    def _es_bandera(node) -> bool:
        """`True` si el <img> es la banderita de idioma de la tarjeta, no la portada.

        Cada tarjeta abre con `<div class="cat_flag"><img class="thumb_flag"
        src="/images/esp.png">` ANTES del `<div class="inner_thumb">` que lleva la
        portada real. Coger el primer <img> del contenedor devolvia esa bandera, asi
        que el listado entero salia con `esp.png` / `uk_usa.png` de portada.

        Se descarta por clase y por ruta: `thumb_flag` es la clase del tema y
        `/images/` es la carpeta de assets estaticos del sitio, mientras que las
        portadas viven siempre en el CDN (`m10.imhentai.xxx/...`).
        """
        clases = node.attrs.get("class", "").split()
        if "thumb_flag" in clases:
            return True
        padre = node.parent
        saltos = 0
        while padre is not None and saltos < 2:
            if "cat_flag" in padre.attrs.get("class", "").split():
                return True
            padre = padre.parent
            saltos += 1
        for clave in ("data-src", "data-lazy-src", "src"):
            valor = node.attrs.get(clave, "").strip()
            # El `src` inicial es un SVG en data: URI (el placeholder del lazy-load);
            # no delata nada, asi que solo se mira la ruta de assets del sitio.
            if valor.startswith("data:"):
                continue
            if valor and "/images/" in urlparse(valor).path:
                return True
        return False

    def _portada(self, item, base: str) -> str | None:
        image = _first(
            item,
            lambda node: node.tag == "img" and not self._es_bandera(node),
        )
        # Si en la tarjeta SOLO habia banderas se cae al comportamiento anterior: es
        # preferible una portada equivocada a quedarse sin ninguna.
        image = image or _first(item, lambda node: node.tag == "img")
        return _image_url(image, base) if image else None

    def _series(self, html: str, base: str) -> list[SourceSeries]:
        root = _parse_html(html)
        classes = {"thumb", "preview_item", "gallery_item"}
        result: list[SourceSeries] = []
        for item in (
            node
            for node in root.descendants()
            if classes.intersection(node.attrs.get("class", "").split())
        ):
            # El PRIMER <a> de la tarjeta es el de la categoria (`/category/doujinshi/`),
            # no el de la galeria: quedarse con el hacia que las 25 tarjetas de la pagina
            # compartieran titulo ("Doujinshi", "Western") y el dedupe final las colapsara
            # a 4 o 5. Se prefiere el enlace que apunta a una galeria.
            enlaces = [
                node for node in item.descendants("a") if node.attrs.get("href")
            ]
            link = next(
                (node for node in enlaces if self._es_enlace_de_galeria(node.attrs["href"])),
                # Sin enlace de galeria reconocible se descarta la tarjeta: caer al
                # primer <a> devolvia la categoria o el idioma, y el dedupe final
                # colapsaba la pagina entera a 2-4 entradas falsas.
                None,
            )
            caption = _first(
                item,
                lambda node: any(
                    name in node.attrs.get("class", "").split()
                    # `gallery_title` es el titulo real de la obra; `gallery_cat` es la
                    # categoria y se descarta a proposito.
                    for name in ("gallery_title", "caption", "title", "tag_name")
                )
                and node.text(),
            )
            title = caption.text() if caption else link.text() if link else ""
            if link and title:
                source_id = urljoin(base, link.attrs["href"])
                result.append(
                    SourceSeries(
                        source_id=source_id,
                        title=title,
                        source_name=self.name,
                        cover_url=self._portada(item, base),
                        web_url=source_id,
                    )
                )
        return list({item.source_id: item for item in result}.values())

    async def _catalog(self, popular: bool, page: int) -> list[SourceSeries]:
        path = self.base_url
        if self.manga_language:
            path += f"/language/{self.manga_language}"
        if popular:
            path += "/popular"
        # La barra final NO es opcional: `/language/spanish/popular` devuelve 404 y solo
        # `/language/spanish/popular/` responde el listado. El engine la omitia, asi que
        # el catalogo entero salia vacio en las 8 variantes.
        if not path.endswith("/"):
            path += "/"
        response = await self._request("GET", path, params={"page": max(page, 1)})
        response.raise_for_status()
        return self._series(response.text, path)

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        response = await self._request(
            "GET",
            f"{self.base_url}/search/",
            params={"q": query.strip(), "key": query.strip(), "page": 1},
        )
        response.raise_for_status()
        return self._series(response.text, self.base_url)[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"}:
            return []
        if kind == "popular":
            try:
                return await self._catalog(True, page)
            except Exception as error:
                if getattr(getattr(error, "response", None), "status_code", None) != 404:
                    raise
        return await self._catalog(False, page)

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        """Ficha de la galeria.

        El `details` heredado de Madara busca `post-title`, `summary_image` y
        `post-content_item`, que este tema no emite: la ficha salia entera vacia
        (sin portada, sin autor, sin tags).

        Los seis sitios del tema tienen markups distintos para la lista de etiquetas
        (`span.tags_text` + `a.tag` en imhentai, `div.tags` + `span.badge` en
        asmhentai, `span.info_txt` + `a.gp_btn_tag` en hentaizap, `li.tags` +
        `span.tag_name` en nhentai.xxx), asi que NO se clasifica por clase CSS sino
        por el prefijo del href, que si es uniforme: `/tag/`, `/artist/`, `/parody/`,
        `/category/`. El contador de cada etiqueta va en un `<span>` hijo y se
        descuenta del texto para no acabar con "nakadashi 225889".
        """
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        base = str(response.url)

        titulo = _first(root, lambda node: node.tag == "h1")
        subtitulo = _first(root, lambda node: node.tag == "p" and node.has_class("subtitle"))
        # La portada de la ficha es `.../cover.jpg`; el resto de <img> son las
        # miniaturas `1t.jpg`, `2t.jpg`... del previsualizador.
        portada = _first(
            root,
            lambda node: node.tag == "img"
            and not self._es_bandera(node)
            and "cover" in _image_url(node, base).rsplit("/", 1)[-1],
        )
        portada = portada or _first(
            root,
            lambda node: node.tag == "img"
            and node.has_class("lazy")
            and not self._es_bandera(node),
        )

        def campo(*prefijos: str) -> list[str]:
            valores: list[str] = []
            for enlace in root.descendants("a"):
                ruta = urlparse(enlace.attrs.get("href", "")).path
                if not any(ruta.startswith(f"/{prefijo}/") for prefijo in prefijos):
                    continue
                texto = enlace.text()
                for hijo in enlace.descendants("span"):
                    # Solo se descuentan los <span> HOJA: en asmhentai el contador va
                    # dentro de `<span class="badge tag">`, que envuelve tambien al
                    # nombre, y recortar el envoltorio dejaba la etiqueta vacia.
                    if any(True for _ in hijo.descendants("span")):
                        continue
                    clases = hijo.attrs.get("class", "").split()
                    if any(
                        marca in clase
                        for clase in clases
                        for marca in ("badge", "count")
                    ):
                        texto = texto.replace(hijo.text(), " ")
                if texto := " ".join(texto.split()):
                    valores.append(texto)
            return valores

        artistas = campo("artist")
        etiquetas = [*campo("tag"), *campo("category"), *campo("parody")]
        return SourceSeries(
            source_id=series_id,
            title=titulo.text().strip() if titulo else (
                series.title if isinstance(series, SourceSeries)
                else series_id.rstrip("/").rsplit("/", 1)[-1]
            ),
            source_name=self.name,
            cover_url=_image_url(portada, base) if portada else (
                series.cover_url if isinstance(series, SourceSeries) else None
            ),
            description=subtitulo.text().strip() if subtitulo else None,
            author=", ".join(artistas) or None,
            artist=", ".join(artistas) or None,
            content_tags=tuple(dict.fromkeys(etiquetas)),
            web_url=base,
            metadata=series.metadata if isinstance(series, SourceSeries) else {},
        )

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        return [
            SourceChapter(
                source_id=series_id,
                title="Chapter",
                series_id=series_id,
                source_name=self.name,
            )
        ]

    @staticmethod
    def _inputs(root) -> dict[str, str]:
        return {
            node.attrs["id"]: node.attrs.get("value", "")
            for node in root.descendants("input")
            if node.attrs.get("id")
        }

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        response = await self._request("GET", chapter_id)
        response.raise_for_status()
        root = _parse_html(response.text)
        inputs = self._inputs(root)
        scripts = "\n".join(node.text() for node in root.descendants("script"))
        encoded = re.search(r"\$\.parseJSON\('(.+?)'\)", scripts, re.DOTALL)
        urls: list[str] = []
        if encoded:
            payload = json.loads(encoded.group(1).encode().decode("unicode_escape"))
            # nhentai.xxx no emite `{"1": "j,1280,1850", ...}` como los demas, sino
            # `{"fl": {...paginas...}, "th": {...miniaturas...}, "ct": {...portada...}}`.
            # Iterando el nivel 1 salian 3 "paginas" llamadas fl/th/ct. Se baja al
            # sub-diccionario de las imagenes completas cuando el payload viene anidado.
            if payload and all(isinstance(valor, dict) for valor in payload.values()):
                payload = payload.get("fl") or max(payload.values(), key=len)
            load_dir = inputs.get("load_dir", "").strip("/")
            load_id = inputs.get("load_id", "")
            server_number = inputs.get("load_server", "")
            # El host del CDN se deduce de la portada. NO se busca por clase: en
            # hentaifox el <img> de la ficha no lleva ninguna (`<img src=".../cover.jpg">`),
            # asi que el filtro `cover|img-responsive` no encontraba nada, se caia al
            # host del sitio y las 137 paginas apuntaban a `hentaifox.com/...` -> 404.
            # Se busca por el nombre del archivo, que si es constante en los 8 sitios.
            cover = _first(
                root,
                lambda node: node.tag == "img"
                and urlparse(_image_url(node, chapter_id)).path.rsplit("/", 1)[-1].startswith("cover."),
            )
            host_portada = urlparse(_image_url(cover, chapter_id)).hostname if cover else None
            # La portada MANDA sobre `load_server`. Ese input solo dice el numero de
            # servidor, y componerlo como `m{n}.{dominio del sitio}` presupone que el
            # CDN vive bajo el mismo dominio: cierto en imhentai/hentaiera, falso en
            # nhentai.xxx, cuyo CDN es `i2.nhentaimg.com`. Ahi se generaban 199 URLs
            # apuntando a `m2.nhentai.xxx`, un host que ni siquiera resuelve.
            server = host_portada or (
                f"m{server_number}.{urlparse(self.base_url).hostname}"
                if server_number
                else urlparse(self.base_url).hostname
            )
            for key, value in payload.items():
                code = str(value).split(",", 1)[0].strip('"')
                extension = {"p": "png", "b": "bmp", "g": "gif", "w": "webp"}.get(code, "jpg")
                urls.append(f"https://{server}/{load_dir}/{load_id}/{key}.{extension}")
        else:
            # Sin payload JSON (asmhentai) las paginas se derivan de las miniaturas
            # `1t.jpg` -> `1.jpg`. El <img> NO es hijo directo del `.preview_thumb`:
            # va dentro de un `<a>`, asi que mirar solo `node.parent` no encontraba
            # ninguna y la galeria salia con 0 paginas. Se sube por los ancestros.
            for node in root.descendants("img"):
                ancestro = node.parent
                saltos = 0
                contenedor = False
                while ancestro is not None and saltos < 3:
                    if any(
                        name in ancestro.attrs.get("class", "").split()
                        for name in ("gallery_thumb", "preview_thumb")
                    ):
                        contenedor = True
                        break
                    ancestro = ancestro.parent
                    saltos += 1
                if not contenedor:
                    continue
                url = _image_url(node, chapter_id)
                extension = url.rsplit(".", 1)[-1]
                urls.append(url.replace(f"t.{extension}", f".{extension}"))
            # El previsualizador solo pinta las 10 primeras y deja el resto tras un
            # boton "View More". El total real esta en `<input id="t_pages">`, y las
            # URLs son correlativas, asi que se completan sin pedir el ajax: sin esto
            # una galeria de 203 paginas se leia como si tuviera 10.
            total = inputs.get("t_pages", "").strip()
            if urls and total.isdigit() and len(urls) < int(total):
                base_url, _, ultimo = urls[0].rpartition("/")
                numero, punto, extension = ultimo.rpartition(".")
                if numero.isdigit() and punto:
                    urls = [
                        f"{base_url}/{indice}{punto}{extension}"
                        for indice in range(1, int(total) + 1)
                    ]
        return [
            SourcePage(
                source_id=url,
                chapter_id=chapter_id,
                index=index,
                filename=url.rsplit("/", 1)[-1].split("?", 1)[0],
                source_name=self.name,
            )
            for index, url in enumerate(urls, 1)
        ]
