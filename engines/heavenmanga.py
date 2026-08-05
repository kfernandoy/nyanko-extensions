"""Adaptador de HeavenManga."""

import json
import re
from urllib.parse import urljoin

try:
    from .madara import (
        MadaraSource,
        SourceChapter,
        SourceFilter,
        SourcePage,
        SourceSeries,
        _Node,
        _first,
        _image_url,
        _parse_html,
    )
except ImportError:
    pass


class HeavenMangaSource(MadaraSource):
    genre_options: tuple[tuple[str, str], ...] = ()
    alphabet_options: tuple[tuple[str, str], ...] = ()
    list_options: tuple[tuple[str, str], ...] = ()

    def get_filters(self) -> list[SourceFilter]:
        return [
            SourceFilter("genre", "Géneros", "select", list(self.genre_options), ""),
            SourceFilter("alphabet", "Alfabético", "select", list(self.alphabet_options), ""),
            SourceFilter("list", "Lista Completa", "select", list(self.list_options), ""),
        ]

    async def browse(self, kind: str, page: int = 1):
        if kind == "popular":
            response = await self._request(
                "GET", f"{self.base_url}/top", params={"orderby": "views", "page": str(page)},
            )
            response.raise_for_status()
            return self._popular_page(response)
        if kind == "latest":
            response = await self._request(
                "GET", self.base_url,
                **({"params": {"page": str(page)}} if page > 1 else {}),
            )
            response.raise_for_status()
            return self._latest_page(response)
        return {"items": [], "has_more": False}

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        query = query.strip()
        params: dict[str, str] = {}
        if query:
            if len(query) < 3:
                raise ValueError("La búsqueda debe tener al menos 3 caracteres")
            url = f"{self.base_url}/buscar"
            params["query"] = query
            search_page = True
        else:
            values = filters or {}
            url = self.base_url
            if values.get("genre"):
                url += f"/genero/{values['genre']}.html"
            if values.get("alphabet"):
                url += "/letra/manga.html"
                params["alpha"] = str(values["alphabet"])
            if values.get("list"):
                url += f"/{values['list']}"
            search_page = False
        if page > 1:
            params["page"] = str(page)
        response = await self._request("GET", url, **({"params": params} if params else {}))
        response.raise_for_status()
        return self._text_search_page(response) if search_page else self._popular_page(response)

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request("GET", series_id)
        response.raise_for_status()
        root = _parse_html(response.text)
        summary = _first(root, lambda node: node.tag == "div" and node.has_class("tab-summary"))
        genres = tuple(
            node.text().strip()
            for node in (summary.descendants("a") if summary else [])
            if self._has_class_ancestor(node, "genres-content") and node.text().strip()
        )
        image = _first(
            summary or root,
            lambda node: node.tag == "img" and self._has_class_ancestor(node, "summary_image"),
        )
        description_box = _first(
            root,
            lambda node: node.tag == "div" and node.has_class("description-summary"),
        )
        paragraphs = description_box.descendants("p") if description_box else []
        return SourceSeries(
            source_id=series_id,
            title=series.title if isinstance(series, SourceSeries) else series_id.rstrip("/").rsplit("/", 1)[-1],
            source_name=self.name,
            cover_url=_image_url(image, str(response.url)) if image else None,
            description=" ".join(node.text().strip() for node in paragraphs if node.text().strip()) or None,
            content_tags=genres,
            metadata=series.metadata if isinstance(series, SourceSeries) else {},
            web_url=str(response.url),
        )

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        params = {
            "columns[0][data]": "number", "columns[0][orderable]": "true",
            "columns[1][data]": "created_at", "columns[1][searchable]": "true",
            "order[0][column]": "1", "order[0][dir]": "desc", "start": "0", "length": "10000",
        }
        response = await self._request(
            "GET", series_id, params=params, headers={"X-Requested-With": "XMLHttpRequest"},
        )
        response.raise_for_status()
        payload = response.json() if hasattr(response, "json") else json.loads(response.text)
        items = sorted(
            payload.get("data", []),
            key=lambda item: self._number(item.get("slug")),
            reverse=True,
        )
        manga_url = series_id.rstrip("/")
        return [
            SourceChapter(
                source_id=f"{manga_url}/{item['slug']}#{item['id']}",
                title=f"Capítulo: {item['slug']}",
                series_id=series_id,
                source_name=self.name,
                number=self._number(item.get("slug")),
                language=self.language,
                uploaded_at=self._chapter_date(item.get("created_at")),
            )
            for item in items
        ]

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else str(chapter)
        raw_id = chapter_id.rpartition("#")[2]
        if not raw_id:
            raise ValueError("Error al obtener el id del capítulo. Actualice la lista")
        response = await self._request("GET", f"{self.base_url}/manga/leer/{raw_id}")
        response.raise_for_status()
        root = _parse_html(response.text)
        script = next(
            (node for node in root.descendants("script") if "pUrl" in node.text()),
            None,
        )
        if script is None:
            raise ValueError("Script pages no encontrado")
        found = re.search(r"pUrl\s*=\s*(\[[\s\S]*?\])\s*;", script.text())
        if found is None:
            raise ValueError("No se pudo extraer el JSON de las páginas")
        payload = json.loads(re.sub(r",\s*([}\]])", r"\1", found.group(1)))
        return [
            SourcePage(
                source_id=str(item["imgURL"]),
                chapter_id=chapter_id,
                index=index,
                filename=str(item["imgURL"]).rsplit("/", 1)[-1].split("?", 1)[0] or f"{index}.jpg",
                source_name=self.name,
            )
            for index, item in enumerate(payload, 1)
        ]

    def _popular_page(self, response) -> dict:
        root = _parse_html(response.text)
        items: list[SourceSeries] = []
        for container in (node for node in root.descendants("div") if node.has_class("page-item-detail")):
            title = _first(container, lambda node: node.tag == "div" and node.has_class("manga-name"))
            anchor = _first(container, lambda node: node.tag == "a" and bool(node.attrs.get("href")))
            image = _first(container, lambda node: node.tag == "img")
            if title is None or anchor is None:
                continue
            source_id = urljoin(str(response.url), anchor.attrs["href"])
            items.append(SourceSeries(
                source_id=source_id, title=title.text().strip(), source_name=self.name,
                cover_url=_image_url(image, str(response.url)) if image else None, web_url=source_id,
            ))
        return {"items": items, "has_more": self._has_next(root)}

    def _latest_page(self, response) -> dict:
        root = _parse_html(response.text)
        items: list[SourceSeries] = []
        seen: set[str] = set()
        loop = _first(
            root,
            lambda node: node.tag == "div" and node.attrs.get("id") == "loop-content"
            and node.parent is not None and node.parent.tag == "div" and node.parent.has_class("col-lg-8"),
        )
        for container in loop.descendants("div") if loop else []:
            if not container.has_class("list-group-item"):
                continue
            if any("Novela" in self._own_text(node) for node in container.descendants("div")):
                continue
            anchor = _first(container, lambda node: node.tag == "a" and bool(node.attrs.get("href")))
            if anchor is None:
                continue
            source_id = urljoin(str(response.url), anchor.attrs["href"]).rstrip("/")
            if source_id in seen:
                continue
            seen.add(source_id)
            caption = _first(anchor, lambda node: node.has_class("captitle"))
            title = caption.text().strip() if caption else anchor.text().strip()
            items.append(SourceSeries(
                source_id=source_id, title=title, source_name=self.name,
                cover_url=f"{source_id.replace('/manga/', '/uploads/manga/')}/cover/cover_250x350.jpg",
                web_url=source_id,
            ))
        return {"items": items, "has_more": self._has_next(root)}

    def _text_search_page(self, response) -> dict:
        root = _parse_html(response.text)
        items: list[SourceSeries] = []
        for container in (node for node in root.descendants("div") if node.has_class("c-tabs-item__content")):
            heading = _first(container, lambda node: node.tag == "h4")
            anchor = _first(heading or container, lambda node: node.tag == "a" and bool(node.attrs.get("href")))
            image = _first(container, lambda node: node.tag == "img")
            if anchor is None:
                continue
            source_id = urljoin(str(response.url), anchor.attrs["href"])
            items.append(SourceSeries(
                source_id=source_id, title=anchor.text().strip(), source_name=self.name,
                cover_url=urljoin(str(response.url), image.attrs.get("data-src", "")) if image else None,
                web_url=source_id,
            ))
        return {"items": items, "has_more": self._has_next(root)}

    @staticmethod
    def _has_next(root: _Node) -> bool:
        return any(
            node.tag == "a" and node.attrs.get("rel") == "next"
            and node.parent is not None and node.parent.parent is not None
            and node.parent.parent.tag == "ul" and node.parent.parent.has_class("pagination")
            for node in root.descendants("a")
        )

    @staticmethod
    def _own_text(node: _Node) -> str:
        return " ".join(child.strip() for child in node.children if isinstance(child, str) and child.strip())

    @staticmethod
    def _number(value) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _chapter_date(value) -> str | None:
        from datetime import datetime
        try:
            return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S").isoformat()
        except (TypeError, ValueError):
            return None
