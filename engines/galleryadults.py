"""Implementación HTML común de GalleryAdults."""

import json
import re
from urllib.parse import urljoin, urlparse

try:
    from .madara import MadaraSource, SourceChapter, SourcePage, SourceSeries, _first, _image_url, _parse_html
except ImportError:
    pass


class GalleryAdultsSource(MadaraSource):
    manga_language = ""
    profile = ""

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
                (node for node in enlaces if "/gallery/" in node.attrs["href"]),
                enlaces[0] if enlaces else None,
            )
            image = _first(item, lambda node: node.tag == "img")
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
                        cover_url=_image_url(image, base) if image else None,
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
        return await self._catalog(kind == "popular", page)

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
            load_dir = inputs.get("load_dir", "").strip("/")
            load_id = inputs.get("load_id", "")
            server_number = inputs.get("load_server", "")
            cover = _first(root, lambda node: node.tag == "img" and any(name in node.attrs.get("class", "").split() for name in ("cover", "img-responsive")))
            server = (
                f"m{server_number}.{urlparse(self.base_url).hostname}"
                if server_number
                else urlparse(_image_url(cover, chapter_id)).hostname if cover else urlparse(self.base_url).hostname
            )
            for key, value in payload.items():
                code = str(value).split(",", 1)[0].strip('"')
                extension = {"p": "png", "b": "bmp", "g": "gif", "w": "webp"}.get(code, "jpg")
                urls.append(f"https://{server}/{load_dir}/{load_id}/{key}.{extension}")
        else:
            for node in root.descendants("img"):
                if node.parent and any(
                    name in node.parent.attrs.get("class", "").split()
                    for name in ("gallery_thumb", "preview_thumb")
                ):
                    url = _image_url(node, chapter_id)
                    extension = url.rsplit(".", 1)[-1]
                    urls.append(url.replace(f"t.{extension}", f".{extension}"))
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
