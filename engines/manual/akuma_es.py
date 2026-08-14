try:
    from .base import FuenteBaseSource, _Node, _TreeParser
except ImportError:
    pass

class FuenteBaseSource:
    pass


class AkumaSource(FuenteBaseSource):
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
    name = 'akuma_es'
    display_name = 'Akuma'
    base_url = 'https://akuma.moe'
    language = 'es'
    # El sitio corta las busquedas seguidas: "Please wait at least 2 seconds between
    # searches" y devuelve una pagina de aviso con 0 resultados en vez de un error. Con
    # 120 (una cada 0.5 s) la busqueda salia siempre vacia; 30 son los 2 s que pide.
    requests_per_minute = 30
    supports_latest = False

    _next_hash = None
    _csrf_token = None
    _akuma_lang_map = {
        "zh": "chinese",
        "cs": "czech",
        "nl": "dutch",
        "en": "english",
        "eo": "esperanto",
        "et": "estonian",
        "fr": "french",
        "de": "german",
        "hu": "hungarian",
        "it": "italian",
        "ko": "korean",
        "pl": "polish",
        "pt": "portuguese",
        "ru": "russian",
        "es": "spanish",
        "tr": "turkish",
        "vi": "vietnamese",
        "ar": "arabic",
        "da": "danish",
        "hi": "hindi",
        "id": "indonesian",
        "jv": "javanese",
        "uk": "ukrainian",
        "ca": "catalan",
        "ceb": "cebuano",
        "ja": "japanese",
        "all": ""
    }

    def get_preferences(self) -> list[SourcePreference]:
        return [
            SourcePreference(
                type="checkbox",
                id="pref_title",
                name="Display manga title as full title",
                default=False
            )
        ]

    def get_filters(self) -> list[SourceFilter]:
        return [
            SourceFilter(type="text", id="female", name="Female Tags (Separate tags with commas (,), Prepend with dash (-) to exclude)"),
            SourceFilter(type="text", id="male", name="Male Tags"),
            SourceFilter(type="text", id="other", name="Other Tags"),
            SourceFilter(
                type="tri_state",
                id="categories",
                name="Categories",
                options=[
                    {"value": "Doujinshi", "name": "Doujinshi"},
                    {"value": "Manga", "name": "Manga"},
                    {"value": "Image Set", "name": "Image Set"},
                    {"value": "Artist CG", "name": "Artist CG"},
                    {"value": "Game CG", "name": "Game CG"},
                    {"value": "Western", "name": "Western"},
                    {"value": "Non-H", "name": "Non-H"},
                    {"value": "Cosplay", "name": "Cosplay"},
                    {"value": "Misc", "name": "Misc"},
                ],
                default={}
            ),
            SourceFilter(type="text", id="group", name="Groups"),
            SourceFilter(type="text", id="artist", name="Artists"),
            SourceFilter(type="text", id="parody", name="Parody"),
            SourceFilter(type="text", id="character", name="Characters"),
            SourceFilter(
                type="select",
                id="options",
                name="Search in favorites, read, or commented",
                options=[
                    {"value": "", "name": "None"},
                    {"value": "favorited", "name": "Favorited only"},
                    {"value": "read", "name": "Read only"},
                    {"value": "commented", "name": "Commented only"},
                ],
                default=""
            ),
        ]

    async def _get_csrf_token(self) -> str:
        response = await self._request("GET", self.base_url)
        response.raise_for_status()
        root = _parse_html(response.text)
        meta = _first(
            root,
            lambda node: node.tag == "meta" and "csrf-token" in node.attrs.get("name", ""),
        )
        token = meta.attrs.get("content", "") if meta else ""
        if not token:
            raise ValueError("No se encontró el token CSRF")
        self._csrf_token = token
        return token

    async def _post(self, params: dict, data: dict):
        token = self._csrf_token or await self._get_csrf_token()
        headers = {"X-Requested-With": "XMLHttpRequest", "X-CSRF-TOKEN": token}
        # El sitio mide el hueco entre BUSQUEDAS, no entre peticiones: si van a menos de
        # 2 s responde 200 con "Please wait at least 2 seconds between searches" y cero
        # resultados, asi que la busqueda salia siempre vacia detras del catalogo. El rpm
        # global no basta porque el GET del token se come parte del intervalo.
        await self._akuma_esperar_turno()
        response = await self._request(
            "POST", self.base_url, params=params, data=data, headers=headers
        )
        if getattr(response, "status_code", 200) == 419:
            self._csrf_token = None
            headers["X-CSRF-TOKEN"] = await self._get_csrf_token()
            response = await self._request(
                "POST", self.base_url, params=params, data=data, headers=headers
            )
        response.raise_for_status()
        return response

    _AKUMA_HUECO_BUSQUEDA = 2.2

    async def _akuma_esperar_turno(self) -> None:
        """Deja pasar 2,2 s desde la busqueda anterior (el sitio exige 2).

        Los import van dentro porque el bundle se arma con la cabecera del motor, que no
        trae ni ``asyncio`` ni ``time``.
        """
        import asyncio
        import time

        lock = getattr(self, "_akuma_lock", None)
        if lock is None:
            lock = self._akuma_lock = asyncio.Lock()
        async with lock:
            ultima = getattr(self, "_akuma_ultima", 0.0)
            espera = self._AKUMA_HUECO_BUSQUEDA - (time.monotonic() - ultima)
            if espera > 0:
                await asyncio.sleep(espera)
            self._akuma_ultima = time.monotonic()

    def _parse_akuma_series(self, response, prefs: dict | None) -> dict:
        prefs = prefs or {}
        display_full = prefs.get("pref_title", False)
        html = response.text
        if "Max keywords of 3 exceeded." in html:
            raise ValueError("Se requiere iniciar sesión para usar más de 3 filtros")
        if "Max keywords of 8 exceeded." in html:
            raise ValueError("Sólo se permiten 8 filtros")
        self._next_hash = None
        result = []
        root = _parse_html(html)
        next_anchor = _first(
            root,
            lambda node: node.tag == "a" and "next" in node.attrs.get("rel", "").split(),
        )
        if next_anchor:
            values = parse_qs(urlparse(next_anchor.attrs.get("href", "")).query)
            self._next_hash = (values.get("cursor") or [None])[0]
        for li in root.descendants("li"):
            parent = li.parent
            in_post_loop = False
            while parent:
                if "post-loop" in parent.attrs.get("class", ""):
                    in_post_loop = True
                    break
                parent = parent.parent
            if not in_post_loop:
                continue
                
            a_tag = _first(li, lambda n: n.tag == "a")
            if not a_tag: continue
            
            href = a_tag.attrs.get("href", "")
            
            overlay_title = _first(li, lambda n: "overlay-title" in n.attrs.get("class", ""))
            title = overlay_title.text().replace('"', '').strip() if overlay_title else ""
            
            if not display_full:
                title = re.sub(r"(\[[^]]*]|[({][^)}]*[)}])", "", title).strip()
                
            img_tag = _first(li, lambda n: n.tag == "img")
            img_src = img_tag.attrs.get("src", "") if img_tag else ""
            
            if href and title:
                result.append(
                    SourceSeries(
                        source_id=urljoin(str(response.url), href),
                        title=title,
                        source_name=self.name,
                        cover_url=urljoin(str(response.url), img_src) if img_src else None,
                        web_url=urljoin(str(response.url), href)
                    )
                )
        return {"items": result, "has_more": bool(self._next_hash)}

    async def search(self, query: str, filters: dict | None = None, page: int = 1):
        filters = filters or {}
        if query.startswith("https://"):
            parsed = urlparse(query)
            if parsed.netloc != urlparse(self.base_url).netloc:
                raise ValueError("URL no soportada")
            parts = [part for part in parsed.path.split("/") if part]
            query = f"id:{parts[1]}" if len(parts) > 1 and parts[0] == "g" else query
        if query.startswith("id:"):
            gallery_url = urljoin(f"{self.base_url}/", f"g/{query[3:]}")
            response = await self._request("GET", gallery_url)
            response.raise_for_status()
            root = _parse_html(response.text)
            title = (_first(root, lambda node: node.has_class("entry-title")) or root).text().strip()
            image = _first(root, lambda node: node.has_class("img-thumbnail"))
            return {
                "items": [SourceSeries(
                    source_id=gallery_url,
                    title=re.sub(r"(\[[^]]*]|[({][^)}]*[)}])", "", title.replace('"', '')).strip(),
                    source_name=self.name,
                    cover_url=_image_url(image, gallery_url) if image else None,
                    web_url=gallery_url,
                )],
                "has_more": False,
            }
        
        final_query = []
        if query:
            final_query.append(query.strip())
            
        akuma_lang = self._akuma_lang_map.get(self.language, "")
        if self.language != "all" and akuma_lang:
            final_query.append(f"language:{akuma_lang}$")
            
        for key in ["female", "male", "other", "group", "artist", "parody", "character"]:
            val = filters.get(key, "")
            if val:
                tags = [t.strip() for t in val.split(",")]
                for t in tags:
                    if not t: continue
                    if t.startswith("-"):
                        clean_tag = t.replace("-", "").strip()
                        final_query.append(f"-{key}:\"{clean_tag}\"")
                    else:
                        final_query.append(f"{key}:\"{t}\"")
                        
        for category, state in (filters.get("categories") or {}).items():
            if state == "include":
                final_query.append(f"category:\"{category}\"")
            elif state == "exclude":
                final_query.append(f"-category:\"{category}\"")
            
        opt = filters.get("options")
        if opt:
            final_query.append(f"opt:{opt}")
            
        q_param = " ".join(final_query)
        
        params = {}
        if page == 1:
            self._next_hash = None
        elif self._next_hash:
            params["cursor"] = self._next_hash
        if q_param:
            params["q"] = q_param
        response = await self._post(params, {"view": "3"})
        return self._parse_akuma_series(response, getattr(self, "preferences", {}))

    async def browse(self, kind: str, page: int = 1):
        if kind != "popular":
            return {"items": [], "has_more": False}
        params = {"q": f"language:{self._akuma_lang_map[self.language]}$"}
        if page == 1:
            self._next_hash = None
        elif self._next_hash:
            params["cursor"] = self._next_hash
        response = await self._post(params, {"view": "3"})
        return self._parse_akuma_series(response, getattr(self, "preferences", {}))

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        response = await self._request("GET", series_id)
        response.raise_for_status()
        root = _parse_html(response.text)
        date = _first(root, lambda node: node.tag == "time" and self._has_class_ancestor(node, "date"))
        return [SourceChapter(
            source_id=f"{str(response.url).rstrip('/')}/1",
            title="Chapter",
            series_id=series_id,
            source_name=self.name,
            number=1.0,
            language=self.language,
            uploaded_at=date.text().strip() if date else None,
        )]

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        response = await self._request("GET", chapter_id)
        response.raise_for_status()
        root = _parse_html(response.text)
        select = _first(root, lambda node: node.has_class("nav-select"))
        options = select.descendants("option") if select else []
        try:
            total = int(options[-1].attrs.get("value", "0"))
        except (IndexError, ValueError):
            return []
        base = str(response.url).rsplit("/", 1)[0]
        return [SourcePage(
            source_id=f"{base}/{index}",
            chapter_id=chapter_id,
            index=index,
            filename=f"{index}.jpg",
            source_name=self.name,
        ) for index in range(1, total + 1)]

    async def page_bytes(self, page: SourcePage | str) -> SourcePageContent:
        page_url = page.source_id if isinstance(page, SourcePage) else page
        response = await self._request("GET", page_url)
        response.raise_for_status()
        root = _parse_html(response.text)
        content = _first(root, lambda node: node.has_class("entry-content"))
        image = _first(content, lambda node: node.tag == "img") if content else None
        if image is None:
            raise SourceNotFoundError("Akuma no publicó la imagen de la página")
        image_url = _image_url(image, str(response.url))
        image_response = await self._request("GET", image_url, headers={"Referer": page_url})
        image_response.raise_for_status()
        return SourcePageContent(
            media_type=image_response.headers.get("Content-Type", "image/jpeg"),
            chunks=iter([image_response.content]),
        )



SOURCE = AkumaSource
