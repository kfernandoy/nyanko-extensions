try:
    from .madara import (
        MadaraSource, _Node, _TreeParser
    )
except ImportError:
    pass

class MadaraSource:
    pass


class HeavenmangaSource(MadaraSource):
    search_paths: tuple[str, ...] = ("search", "")
    popular_paths: tuple[str, ...] = ("series", "manga", "comics", "popular", "")
    latest_paths: tuple[str, ...] = ("latest", "updates", "series", "manga", "")

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        for path in self.search_paths:
            for key in ("q", "query", "s", "keyword"):
                try:
                    response = await self._request(
                        "GET",
                        urljoin(f"{self.base_url}/", path),
                        params={key: query.strip(), "page": "1"},
                    )
                    if getattr(response, "status_code", 200) >= 400:
                        continue
                    values = self._adaptive_series(response)
                    if values:
                        return values[:limit]
                except Exception:
                    continue
        return []

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"}:
            return []
        paths = self.popular_paths if kind == "popular" else self.latest_paths
        for path in paths:
            try:
                response = await self._request(
                    "GET",
                    urljoin(f"{self.base_url}/", path),
                    params={"page": str(page)},
                )
                if getattr(response, "status_code", 200) >= 400:
                    continue
                values = self._adaptive_series(response)
                if values:
                    return values
            except Exception:
                continue
        return []

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        result: list[SourceChapter] = []
        for anchor in root.descendants("a"):
            href = anchor.attrs.get("href", "")
            title = anchor.text().strip() or anchor.attrs.get("title", "").strip()
            marker = f"{href} {title}".lower()
            if not href or not any(value in marker for value in ("chapter", "chap", "capitulo", "capítulo", "episode", "bolum", "read/")):
                continue
            found = re.search(r"\d+(?:\.\d+)?", title)
            result.append(
                SourceChapter(
                    source_id=urljoin(str(response.url), href),
                    title=title or "Capítulo",
                    series_id=series_id,
                    source_name=self.name,
                    number=float(found.group()) if found else None,
                )
            )
        if not result:
            try:
                payload = response.json()
            except (ValueError, AttributeError):
                payload = None
            for item in self._walk_dicts(payload):
                title = str(item.get("title") or item.get("name") or "")
                item_id = item.get("url") or item.get("slug") or item.get("id")
                if not title or item_id is None or "chap" not in json.dumps(item).lower():
                    continue
                found = re.search(r"\d+(?:\.\d+)?", title)
                result.append(
                    SourceChapter(
                        source_id=urljoin(str(response.url), str(item_id)),
                        title=title,
                        series_id=series_id,
                        source_name=self.name,
                        number=float(found.group()) if found else None,
                    )
                )
        return list({item.source_id: item for item in result}.values())

    def _adaptive_series(self, response) -> list[SourceSeries]:
        root = _parse_html(response.text)
        result: list[SourceSeries] = []
        seen: set[str] = set()
        for anchor in root.descendants("a"):
            href = anchor.attrs.get("href", "")
            title = anchor.attrs.get("title", "").strip() or anchor.text().strip()
            parent = anchor.parent
            marker = ""
            while parent is not None:
                marker += f" {parent.attrs.get('id', '')} {parent.attrs.get('class', '')}"
                parent = parent.parent
            if not href or not title or not any(value in marker.lower() for value in ("manga", "comic", "series", "novel", "item", "book")):
                continue
            source_id = urljoin(str(response.url), href)
            if source_id not in seen:
                seen.add(source_id)
                image = _first(anchor, lambda node: node.tag == "img")
                if image is None and anchor.parent is not None:
                    image = _first(anchor.parent, lambda node: node.tag == "img")
                result.append(
                    SourceSeries(
                        source_id=source_id,
                        title=title,
                        source_name=self.name,
                        cover_url=(
                            _image_url(image, str(response.url)) if image else None
                        ),
                        web_url=source_id,
                    )
                )
        if result:
            return result
        try:
            payload = response.json()
        except (ValueError, AttributeError):
            return []
        for item in self._walk_dicts(payload):
            title = item.get("title") or item.get("name")
            item_id = item.get("url") or item.get("href") or item.get("slug") or item.get("id")
            if title and item_id is not None:
                source_id = urljoin(str(response.url), str(item_id))
                if source_id not in seen:
                    seen.add(source_id)
                    cover = (
                        item.get("cover_url")
                        or item.get("cover")
                        or item.get("thumbnail")
                        or item.get("image")
                    )
                    result.append(
                        SourceSeries(
                            source_id=source_id,
                            title=str(title),
                            source_name=self.name,
                            cover_url=(
                                urljoin(str(response.url), cover)
                                if isinstance(cover, str)
                                else None
                            ),
                            web_url=source_id,
                        )
                    )
        return result

    @staticmethod
    def _walk_dicts(value):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from GenericSource._walk_dicts(child)
        elif isinstance(value, list):
            for child in value:
                yield from GenericSource._walk_dicts(child)



SOURCE = HeavenmangaSource
