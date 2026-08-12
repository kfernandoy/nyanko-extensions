"""Ficha HTML compartida por Madara y temas con marcado compatible."""

import re
from urllib.parse import urljoin

try:
    from .base import FuenteBaseSource, SourceSeries, _Node, _first, _image_url, _parse_html, _style_image_url
except ImportError:
    pass


class MadaraDetailsSource(FuenteBaseSource):
    """Añade solo la ficha; cada motor conserva su catalogo, capitulos y lector."""

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        title_node = _first(
            root,
            lambda node: node.tag in {"h1", "h3"}
            and (
                self._has_class_ancestor(node, "post-title")
                or self._has_id_ancestor(node, "manga-title")
                or node.has_class("post-title")
                or node.has_class("mb-2")
            ),
        )
        title = title_node.text().strip() if title_node else (
            series.title if isinstance(series, SourceSeries) else series_id.rstrip("/").rsplit("/", 1)[-1]
        )
        image = _first(root, lambda node: node.tag == "img" and self._has_class_ancestor(node, "summary_image"))
        description_node = _first(
            root,
            lambda node: node.has_class("summary__content")
            and self._has_class_ancestor(node, "description-summary")
            or node.has_class("manga-excerpt")
            or node.has_class("mv-synopsis")
            or node.has_class("summary-container")
            or node.has_class("modal-contenido") and self._has_class_ancestor(node, "c-page__content"),
        )
        paragraphs = description_node.descendants("p") if description_node else []
        description = (
            "\n\n".join(paragraph.text().strip() for paragraph in paragraphs if paragraph.text().strip())
            if paragraphs else description_node.text().strip() if description_node else ""
        )
        authors = self._detail_links(root, ("author-content", "manga-authors"))
        artists = self._detail_links(root, ("artist-content",))
        status_text = ""
        for item in root.descendants("div"):
            if not item.has_class("post-content_item") or not self._has_class_ancestor(item, "summary_content"):
                continue
            heading = _first(
                item,
                lambda node: node.has_class("summary-heading")
                and any(label in node.text().casefold() for label in ("status", "estado")),
            )
            value = _first(item, lambda node: node.has_class("summary-content"))
            if heading and value:
                status_text = value.text().strip()
        genres = [
            node.text().strip()
            for node in root.descendants("a")
            if self._has_class_ancestor(node, "genres-content") and node.text().strip()
        ]
        for item in root.descendants():
            if not item.has_class("post-content_item"):
                continue
            own = " ".join(child.strip() for child in item.children if isinstance(child, str) and child.strip())
            heading = _first(item, lambda node: node.has_class("summary-heading"))
            label = f"{own} {heading.text() if heading else ''}"
            value = _first(item, lambda node: node.has_class("summary-content"))
            if not value or not value.text().strip():
                continue
            if "Type" in label and value.text().strip() != "-":
                genres.append(value.text().strip())
            elif "Alt" in label:
                description = f"{description}\n\nAlternative name(s): {value.text().strip()}".strip()
        genres = list(dict.fromkeys(genre for genre in genres if genre))
        cover_url = _image_url(image, str(response.url)) if image else None
        if not (cover_url and description and genres and status_text):
            alternative = self._tailwind_details(root, str(response.url))
            slug = series_id.rstrip("/").rsplit("/", 1)[-1]
            title = title if title and title != slug else alternative.get("title") or title
            cover_url = cover_url or alternative.get("cover_url")
            description = description or alternative.get("description", "")
            status_text = status_text or alternative.get("status", "")
            if not genres:
                genres = alternative.get("genres", [])
            if not authors and alternative.get("author"):
                authors = [alternative["author"]]
        return SourceSeries(
            source_id=series_id,
            title=title,
            source_name=self.name,
            cover_url=cover_url,
            description=description or None,
            author=", ".join(authors) or None,
            artist=", ".join(artists) or None,
            status=self._madara_status(status_text),
            content_tags=tuple(genres),
            metadata=series.metadata if isinstance(series, SourceSeries) else {},
            web_url=str(response.url),
        )

    def _tailwind_details(self, root: _Node, page_url: str) -> dict:
        data: dict = {}
        title = _first(root, lambda node: node.tag == "h1")
        if title and title.text().strip():
            data["title"] = title.text().strip()
        synopsis = _first(root, lambda node: node.attrs.get("id") == "expand_content")
        if synopsis and synopsis.text().strip():
            data["description"] = synopsis.text().strip()
        for node in root.descendants("div"):
            if any("0.75/1" in value for value in node.attrs.get("class", "").split()):
                if url := _style_image_url(node, page_url):
                    data["cover_url"] = url
                    break
        genres: list[str] = []
        seen: set[str] = set()
        for node in root.descendants():
            if node.attrs.get("id") == "expand_content":
                break
            if not any("rounded" in value for value in node.attrs.get("class", "").split()):
                continue
            text = node.text().strip()
            if not text or len(text) >= 80:
                continue
            key = text.casefold()
            if key in self._ESTADOS_BADGE:
                data.setdefault("status", text)
            elif key not in seen:
                seen.add(key)
                genres.append(text)
        if genres:
            data["genres"] = genres
        ld_json = _first(
            root,
            lambda node: node.tag == "script" and node.attrs.get("type") == "application/ld+json",
        )
        if ld_json is not None:
            author = re.search(r'"author"\s*:\s*\{[^}]*?"name"\s*:\s*"([^"]+)"', ld_json.text())
            if author:
                data["author"] = author.group(1).strip()
        return data

    @classmethod
    def _detail_links(cls, root: _Node, containers: tuple[str, ...]) -> list[str]:
        return [
            node.text().strip()
            for node in root.descendants("a")
            if any(cls._has_class_ancestor(node, name) for name in containers)
            and node.text().strip()
            and "updating" not in node.text().casefold()
            and "atualizando" not in node.text().casefold()
        ]
