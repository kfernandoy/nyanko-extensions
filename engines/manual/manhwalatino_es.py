try:
    from .madara import MadaraSource, _Node, _TreeParser
except ImportError:
    pass

class MadaraSource:
    pass



class ManhwaLatinoSource(MadaraSource):
    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        series_url = urljoin(f"{self.base_url}/", series_id)
        response = await self._request("GET", series_url)
        response.raise_for_status()
        root = _parse_html(response.text)
        if not self._chapter_nodes(root):
            holder = _first(root, lambda node: node.attrs.get("id", "").startswith("manga-chapters-holder"))
            if holder is not None:
                response = await self._request(
                    "POST", f"{series_url.rstrip('/')}/ajax/chapters",
                    headers={"X-Requested-With": "XMLHttpRequest"},
                )
                response.raise_for_status()
                root = _parse_html(response.text)

        result = []
        page = 1
        while True:
            for item in self._chapter_nodes(root):
                box = _first(item, lambda node: node.tag == "div" and node.has_class("mini-letters"))
                anchor = _first(box, lambda node: node.tag == "a" and bool(node.attrs.get("href"))) if box else None
                if anchor is None:
                    continue
                whole_text = "".join(
                    child.text() if isinstance(child, _Node) else child for child in anchor.children
                )
                title = whole_text.split("\n", 1)[-1].strip() or anchor.text().strip()
                image = _first(item, lambda node: node.tag == "img" and not node.has_class("thumb"))
                relative = _first(
                    item,
                    lambda node: node.tag == "a" and node.parent is not None
                    and node.parent.tag == "span" and bool(node.attrs.get("title")),
                )
                date = _first(item, lambda node: node.has_class("chapter-release-date"))
                date_text = (
                    image.attrs.get("alt", "") if image else relative.attrs.get("title", "") if relative
                    else date.text() if date else ""
                )
                url = urljoin(series_url, anchor.attrs["href"]).split("?style=paged", 1)[0]
                if not url.endswith(self.chapter_url_suffix):
                    url += self.chapter_url_suffix
                number = re.search(r"\d+(?:\.\d+)?", title)
                result.append(SourceChapter(
                    source_id=url, title=title or "Capítulo", series_id=series_id,
                    source_name=self.name, number=float(number.group()) if number else None,
                    language=self.language, uploaded_at=self._madara_date(date_text),
                ))
            if not self._latino_has_next(root):
                return result
            page += 1
            response = await self._request("GET", series_url, params={"t": str(page)})
            response.raise_for_status()
            root = _parse_html(response.text)

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else str(chapter)
        response = await self._request("GET", urljoin(f"{self.base_url}/", chapter_id))
        response.raise_for_status()
        urls = [
            _image_url(image, str(response.url))
            for image in _parse_html(response.text).descendants("img")
            if image.has_class("wp-manga-chapter-img") and self._has_class_ancestor(image, "page-break")
        ]
        return [SourcePage(
            source_id=url, chapter_id=chapter_id, index=index,
            filename=urlparse(url).path.rsplit("/", 1)[-1] or f"{index}.jpg", source_name=self.name,
        ) for index, url in enumerate(dict.fromkeys(urls))]

    async def page_bytes(self, page: SourcePage | str) -> SourcePageContent:
        url = page.source_id if isinstance(page, SourcePage) else str(page)
        response = await self._request(
            "GET", url,
            headers={
                "Accept-Encoding": "",
                "Referer": page.chapter_id if isinstance(page, SourcePage) else self.base_url,
            },
        )
        response.raise_for_status()
        media_type = response.headers.get("Content-Type", "image/jpeg")
        if "application/octet-stream" in media_type.casefold():
            media_type = "image/jpeg"
        return SourcePageContent(media_type=media_type, chunks=iter([response.content]))

    @staticmethod
    def _latino_has_next(root: _Node) -> bool:
        for current in root.descendants("span"):
            parent = current.parent
            if not current.has_class("current") or parent is None or not parent.has_class("pagination"):
                continue
            index = parent.children.index(current)
            if any(isinstance(sibling, _Node) and sibling.tag == "span" for sibling in parent.children[index + 1:]):
                return True
        return False
class GeneratedMadaraSource(ManhwaLatinoSource):
    name = 'manhwalatino_es'
    display_name = 'Manhwa-Latino'
    base_url = 'https://manhwa-latino.com'
    language = 'es'
    manga_substring = 'manga'
    load_more = 'auto'
    use_new_chapter_endpoint = True
    chapter_url_suffix = '?style=list'
    supports_latest = True
    requests_per_minute = 30
    pages_profile = 'default'
    extra_headers = {}
    image_headers = {}
    date_format = 'dd/MM/yyyy'
    date_locale = 'es'
    details_profile = 'default'
    content_warning = 'mixed'

SOURCE = GeneratedMadaraSource
