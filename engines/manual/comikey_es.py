try:
    from .base import FuenteBaseSource, _Node, _TreeParser
except ImportError:
    pass

class FuenteBaseSource:
    pass


class ComikeySource(FuenteBaseSource):
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

class GeneratedGenericSource(GenericSource):

    def get_preferences(self) -> list[SourcePreference]:
        # Autogenerated via heuristic port
        data = [
                {
                                "type": "checkbox",
                                "id": "pref_adult",
                                "name": "Show Adult Content",
                                "default": false
                }
]
        return [SourcePreference(**item) for item in data]

    def get_filters(self) -> list[SourceFilter]:
        # Autogenerated via heuristic port
        data = []
        return [SourceFilter(**item) for item in data]

    name = 'comikey_es'
    display_name = 'Comikey'
    base_url = 'https://comikey.com'
    language = 'es'
    requests_per_minute = 180


class ComikeySource(GeneratedGenericSource):
    gundam_url = "https://gundam.comikey.net"

    def __init__(self, fetcher: SourceFetcher | None = None) -> None:
        super().__init__(fetcher)
        from dataclasses import replace
        self.capabilities = replace(self.capabilities, requires_webview=True)

    def get_preferences(self) -> list[SourcePreference]:
        return [SourcePreference(
            "hide_locked_chapters", "Ocultar capitulos bloqueados", "checkbox", default=False,
        )]

    def get_filters(self) -> list[SourceFilter]:
        return [
            SourceFilter("order", "Ordenar por", "sort", [
                ("updated", "Ultima actualizacion"), ("name", "Nombre"),
                ("views", "Popularidad"), ("chapters", "Cantidad de capitulos"),
            ], "views"),
            SourceFilter("direction", "Direccion", "select", [("desc", "Descendente"), ("asc", "Ascendente")], "desc"),
            SourceFilter("filter", "Filtrar por", "select", [
                ("", "Todo"), ("manga", "Manga"), ("webtoon", "Webtoon"),
                ("new", "Nuevo"), ("complete", "Completo"), ("exclusive", "Exclusivo"),
                ("simulpub", "Simulpub"),
            ], ""),
        ]

    @staticmethod
    def _inside(node, tag: str | None = None, class_name: str | None = None) -> bool:
        parent = node.parent
        while parent is not None:
            if (tag is None or parent.tag == tag) and (class_name is None or parent.has_class(class_name)):
                return True
            parent = parent.parent
        return False

    @staticmethod
    def _has_next(root) -> bool:
        return any(
            node.tag == "li" and node.has_class("next-page") and not node.has_class("disabled")
            and ComikeySource._inside(node, "ul", "pagination")
            for node in root.descendants()
        )

    def _listing(self, response) -> dict:
        root = _parse_html(response.text)
        items = []
        for item in root.descendants("li"):
            parent = item.parent
            holder = parent.parent if parent is not None else None
            if parent is None or parent.tag != "ul" or holder is None or holder.tag != "div" or not holder.has_class("series-listing") or holder.attrs.get("data-view") != "list":
                continue
            data = _first(item, lambda node: node.tag == "div" and node.has_class("series-data"))
            title_box = _first(data, lambda node: node.tag == "span" and node.has_class("title")) if data else None
            anchor = _first(title_box, lambda node: node.tag == "a" and bool(node.attrs.get("href"))) if title_box else None
            if anchor is None:
                continue
            excerpt = _first(item, lambda node: node.tag == "div" and node.has_class("excerpt"))
            description = _first(item, lambda node: node.tag == "div" and node.has_class("desc"))
            text = "\n\n".join(value for value in (
                excerpt.text().strip() if excerpt else "", description.text().strip() if description else "",
            ) if value)
            genres = tuple(
                node.text().strip() for node in item.descendants("a")
                if node.text().strip() and self._inside(node, "ul", "category-listing")
            )
            image_box = _first(item, lambda node: node.tag == "div" and node.has_class("image"))
            image = _first(image_box, lambda node: node.tag == "img") if image_box else None
            source_id = urljoin(str(response.url), anchor.attrs["href"])
            items.append(SourceSeries(
                source_id=source_id, title=anchor.text().strip(), source_name=self.name,
                cover_url=_image_url(image, str(response.url)) if image else None,
                description=text or None, content_tags=genres, web_url=source_id,
            ))
        return {"items": items, "has_more": self._has_next(root)}

    async def browse(self, kind: str, page: int = 1):
        if kind not in {"popular", "latest"}:
            return {"items": [], "has_more": False}
        params = {"page": str(page)}
        if kind == "popular":
            params["order"] = "-views"
        response = await self._request("GET", f"{self.base_url}/comics/", params=params)
        response.raise_for_status()
        return self._listing(response)

    def _details(self, response) -> SourceSeries:
        root = _parse_html(response.text)
        script = _first(root, lambda node: node.tag == "script" and node.attrs.get("id") == "comic")
        if script is None:
            raise ValueError("Comikey no publico los datos de la serie")
        data = json.loads(script.text())
        source_id = urljoin(str(response.url), str(data.get("link", "")))
        tags = [str(item.get("name", "")) for item in data.get("tags", []) if item.get("name")]
        tags.extend({0: ["Comic"], 1: ["Manga"], 2: ["Webtoon"]}.get(data.get("format"), []))
        status = {
            1: "completed", 3: "hiatus",
            **{value: "ongoing" for value in range(4, 15)},
        }.get(data.get("update_status"))
        if data.get("update_status") == 0:
            update = str(data.get("update_text", "")).lower()
            status = "ongoing" if update.startswith("toda") else "hiatus" if update.startswith(("em pausa", "hiato")) else None
        return SourceSeries(
            source_id=source_id, title=str(data.get("name", "")), source_name=self.name,
            cover_url=urljoin(f"{self.base_url}/", str(data.get("full_cover", ""))),
            description=f'"{data.get("excerpt", "")}\"\n\n{data.get("description", "")}'.strip(),
            author=", ".join(str(item.get("name", "")) for item in data.get("author", []) if item.get("name")),
            artist=", ".join(str(item.get("name", "")) for item in data.get("artist", []) if item.get("name")),
            status=status, content_tags=tuple(tags), web_url=source_id,
        )

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        query = query.strip()
        if query.startswith("https://"):
            parsed = urlparse(query)
            if parsed.netloc != urlparse(self.base_url).netloc:
                raise ValueError("URL no compatible")
            response = await self._request("GET", query)
            response.raise_for_status()
            return {"items": [self._details(response)], "has_more": False}
        if query.startswith("slug:"):
            response = await self._request("GET", f"{self.base_url}/comics/{query.removeprefix('slug:').strip('/')}/")
            response.raise_for_status()
            return {"items": [self._details(response)], "has_more": False}
        values = filters or {}
        order = str(values.get("order", "views"))
        if str(values.get("direction", "desc")) == "desc":
            order = f"-{order}"
        params = {"order": order}
        if page > 1:
            params["page"] = str(page)
        if len(query) >= 2:
            params["q"] = query
        if values.get("filter"):
            params["filter"] = str(values["filter"])
        response = await self._request("GET", f"{self.base_url}/comics/", params=params)
        response.raise_for_status()
        return self._listing(response)

    @staticmethod
    def _released(value: str):
        from datetime import datetime, timezone
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            return None

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        from datetime import datetime, timezone
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        comic_script = _first(root, lambda node: node.tag == "script" and node.attrs.get("id") == "comic")
        if comic_script is None:
            return []
        comic = json.loads(comic_script.text())
        parts = [part for part in urlparse(str(response.url)).path.split("/") if part]
        if len(parts) < 3:
            return []
        manga_slug, manga_id = parts[1], parts[2]
        token = None
        for script in root.descendants("script"):
            found = re.search(r'GUNDAM\.token\s*=\s*"([^"]+)";', script.text())
            if found:
                token = found.group(1)
                break
        endpoint = "comic" if token else "comic.public"
        params = {"language": self.language.lower()}
        if token:
            params["token"] = token
        episodes_response = await self._request("GET", f"{self.gundam_url}/{endpoint}/{manga_id}/episodes", params=params)
        episodes_response.raise_for_status()
        payload = episodes_response.json() if hasattr(episodes_response, "json") else json.loads(episodes_response.text)
        hide_locked = bool(getattr(self, "preferences", {}).get("hide_locked_chapters", False))
        prefix = "episode" if comic.get("format") == 2 else "chapter"
        if prefix == "chapter" and self.language != "en":
            prefix = "capitulo-espanol"
        result = []
        for episode in payload.get("episodes", []):
            readable = int(episode.get("finalPrice", 0)) == 0 or bool(episode.get("owned", False))
            if hide_locked and not readable:
                continue
            released = str(episode.get("releasedAt", ""))
            parsed_date = self._released(released)
            if parsed_date is not None and parsed_date > datetime.now(timezone.utc):
                continue
            number = float(episode.get("number", 0))
            e4pid = str(episode.get("id", "")).split("-", 1)[-1]
            number_slug = f"{number:g}".replace(".", "-")
            title = str(episode.get("title", ""))
            if episode.get("subtitle") is not None:
                title += f": {episode['subtitle']}"
            chapter_url = f"{self.base_url}/read/{manga_slug}/{e4pid}/{prefix}-{number_slug}/"
            result.append(SourceChapter(
                source_id=chapter_url, title=title, series_id=series_id, source_name=self.name,
                number=number, language=self.language, uploaded_at=released or None,
            ))
        return list(reversed(result))

    @staticmethod
    def _manifest_pages(manifest: dict, manifest_url: str, act: str, chapter_id: str, source_name: str) -> list[SourcePage]:
        webtoon = manifest.get("metadata", {}).get("readingProgression") == "ttb"
        pages = []
        for index, item in enumerate(manifest.get("readingOrder", [])):
            href = str(item.get("href", ""))
            alternates = item.get("alternate", [])
            if alternates and item.get("height") == 2048 and item.get("type") == "image/jpeg":
                match = next((alt for alt in alternates if alt.get("type") == "image/webp" and int(alt.get("width" if webtoon else "height", 9999)) <= 1536), None)
                if match:
                    href = str(match.get("href", href))
            url = urljoin(manifest_url.rsplit("/", 1)[0] + "/", href)
            if act:
                url += ("&" if "?" in url else "?") + urlencode({"act": act})
            pages.append(SourcePage(
                source_id=url, chapter_id=chapter_id, index=index,
                filename=urlparse(url).path.rsplit("/", 1)[-1] or f"{index}.jpg", source_name=source_name,
            ))
        return pages

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        from secrets import choice
        from string import ascii_letters
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        requested_with = "".join(choice(ascii_letters) for _ in range(14))
        response = await self._request("GET", chapter_id, headers={"X-Requested-With": requested_with})
        response.raise_for_status()
        root = _parse_html(response.text)
        init_node = _first(root, lambda node: node.attrs.get("id") == "lmao-init")
        if init_node is None:
            raise ValueError("El lector de Comikey requiere abrir el capitulo en WebView")
        initial = json.loads(init_node.text())
        manifest_value = initial.get("manifest")
        act = str(initial.get("act", ""))
        if isinstance(manifest_value, dict):
            return self._manifest_pages(manifest_value, str(response.url), act, chapter_id, self.name)
        manifest_url = urljoin(str(response.url), str(manifest_value or ""))
        manifest_response = await self._request("GET", manifest_url)
        manifest_response.raise_for_status()
        try:
            manifest = manifest_response.json() if hasattr(manifest_response, "json") else json.loads(manifest_response.text)
        except (ValueError, json.JSONDecodeError) as exc:
            raise ValueError("El lector cifrado de Comikey requiere WebView y un token App Check vigente") from exc
        return self._manifest_pages(manifest, str(manifest_response.url), act, chapter_id, self.name)


SOURCE = ComikeySource
