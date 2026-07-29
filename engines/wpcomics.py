"""Motor común para WPComics y sus variantes."""

import re
from urllib.parse import urljoin

try:
    from .madara import MadaraSource, SourceChapter, SourcePage, SourceSeries, _first, _image_url, _parse_html
except ImportError:
    pass


class WPComicsSource(MadaraSource):
    popular_path = "hot"
    latest_path = ""
    search_path = "tim-truyen"

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        response = await self._request(
            "GET",
            f"{self.base_url}/{self.search_path.strip('/')}",
            params={"keyword": query.strip(), "page": "1", "sort": "0"},
        )
        response.raise_for_status()
        return self._wp_series(response.text)[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"}:
            return []
        path = self.popular_path if kind == "popular" else self.latest_path
        response = await self._request(
            "GET",
            f"{self.base_url}/{path.strip('/')}" if path else self.base_url,
            params={"page": str(page)} if page > 1 or self.latest_path else {},
        )
        response.raise_for_status()
        return self._wp_series(response.text)

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        result: list[SourceChapter] = []
        for node in root.descendants():
            marker = node.attrs.get("class", "").lower()
            if node.tag not in {"li", "div"} or not any(word in marker for word in ("chapter", "row")):
                continue
            anchor = _first(node, lambda item: item.tag == "a" and bool(item.attrs.get("href")))
            if anchor is None or not any(word in f"{anchor.text()} {anchor.attrs['href']}".lower() for word in ("chapter", "chap", "chuong")):
                continue
            title = anchor.text().strip()
            result.append(
                SourceChapter(
                    source_id=urljoin(str(response.url), anchor.attrs["href"]),
                    title=title,
                    series_id=series_id,
                    source_name=self.name,
                    number=self._chapter_number(title),
                )
            )
        return list({chapter.source_id: chapter for chapter in result}.values())

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        response = await self._request("GET", urljoin(f"{self.base_url}/", chapter_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        urls = [
            _image_url(image, str(response.url))
            for image in root.descendants("img")
            if self._reader_image(image)
        ]
        return [
            SourcePage(url, chapter_id, index, url.rsplit("/", 1)[-1].split("?", 1)[0] or f"{index}.jpg", self.name)
            for index, url in enumerate(dict.fromkeys(filter(None, urls)), 1)
        ]

    def _wp_series(self, html: str) -> list[SourceSeries]:
        root = _parse_html(html)
        result: list[SourceSeries] = []
        seen: set[str] = set()
        for node in root.descendants():
            if not any(node.has_class(value) for value in ("item", "row", "item-manga")):
                continue
            anchor = _first(node, lambda item: item.tag == "a" and bool(item.attrs.get("href")) and (item.text().strip() or item.attrs.get("title")))
            if anchor is None:
                continue
            source_id = urljoin(f"{self.base_url}/", anchor.attrs["href"])
            title = anchor.text().strip() or anchor.attrs.get("title", "").strip()
            if title and source_id not in seen:
                seen.add(source_id)
                result.append(SourceSeries(source_id, title, self.name))
        return result

    @staticmethod
    def _reader_image(node) -> bool:
        parent = node.parent
        while parent is not None:
            marker = f"{parent.attrs.get('id', '')} {parent.attrs.get('class', '')}".lower()
            if any(value in marker for value in ("page-chapter", "blocks-gallery-item", "reading-content")):
                return True
            parent = parent.parent
        return False

    @staticmethod
    def _chapter_number(title: str) -> float | None:
        found = re.search(r"\d+(?:\.\d+)?", title)
        return float(found.group()) if found else None
