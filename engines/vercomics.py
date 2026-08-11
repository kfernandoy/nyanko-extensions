"""Implementación común de VerComics."""

from urllib.parse import urljoin

try:
    from .base import (
        FuenteBaseSource,
        SourceChapter,
        SourcePage,
        SourceSeries,
        _first,
        _image_url,
        _parse_html,
    )
except ImportError:
    pass


class VerComicsSource(FuenteBaseSource):
    url_suffix = ""
    use_suffix_on_search = True
    supports_latest = False

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        path = f"/{self.url_suffix}" if self.use_suffix_on_search and self.url_suffix else ""
        response = await self._request(
            "GET",
            f"{self.base_url}{path}/page/1",
            params={"s": query.strip()},
        )
        response.raise_for_status()
        return self._listing(response.text, str(response.url))[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind != "popular":
            return []
        response = await self._request(
            "GET",
            f"{self.base_url}/{self.url_suffix}/page/{page}".replace("//page", "/page"),
        )
        response.raise_for_status()
        return self._listing(response.text, str(response.url))

    def _listing(self, html: str, response_url: str) -> list[SourceSeries]:
        root = _parse_html(html)
        result: list[SourceSeries] = []
        for entry in (node for node in root.descendants() if node.has_class("entry")):
            anchor = _first(
                entry,
                lambda node: node.tag == "a"
                and node.has_class("popimg")
                and bool(node.attrs.get("href")),
            )
            image = _first(anchor or entry, lambda node: node.tag == "img")
            title = image.attrs.get("alt", "").strip() if image else ""
            if anchor is not None and title:
                result.append(SourceSeries(urljoin(response_url, anchor.attrs["href"]), title, self.name))
        return result

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        title = series.title if isinstance(series, SourceSeries) else "Capítulo único"
        return [
            SourceChapter(
                source_id=series_id,
                title=title,
                series_id=series_id,
                source_name=self.name,
                number=1,
            )
        ]

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        response = await self._request("GET", chapter_id)
        response.raise_for_status()
        root = _parse_html(response.text)
        urls: list[str] = []
        for image in root.descendants("img"):
            node = image
            inside_content = False
            inside_post_images = False
            while node.parent is not None:
                node = node.parent
                inside_content |= node.has_class("wp-content")
                inside_post_images |= node.has_class("post-imgs")
            if (inside_content or inside_post_images) and (url := _image_url(image, str(response.url))):
                urls.append(url)
        return [
            SourcePage(
                source_id=url,
                chapter_id=chapter_id,
                index=index,
                filename=url.rsplit("/", 1)[-1].split("?", 1)[0] or f"{index}.jpg",
                source_name=self.name,
            )
            for index, url in enumerate(urls, 1)
        ]
