try:
    from .madara import (
        MadaraSource, _Node, _TreeParser
    )
except ImportError:
    pass

class MadaraSource:
    pass


def _onisaga_detach(node: _Node) -> None:
    parent = node.parent
    if parent is not None and node in parent.children:
        parent.children.remove(node)


def _onisaga_ancestor(node: _Node, *classes: str) -> _Node | None:
    current = node.parent
    while current is not None:
        if all(current.has_class(name) for name in classes):
            return current
        current = current.parent
    return None


class OnisagaSource(MadaraSource):
    """El sitio es Livewire: hay que reenviar snapshot y token en cada pagina."""

    image_delay_seconds = 2.0

    def __init__(self, fetcher: SourceFetcher | None = None) -> None:
        super().__init__(fetcher)
        self._state: tuple[str, str, str] | None = None  # (url, snapshot, token)
        self._reader_token = ""
        self._last_image_at = 0.0

    @property
    def language_code(self) -> str | None:
        return _ONISAGA_LANGS.get(self.language)

    def get_preferences(self) -> list[SourcePreference]:
        return [
            SourcePreference("pref_nsfw", "Show NSFW / 18+ Content", "checkbox", default=False),
            SourcePreference("pref_type", "Type Filter", "select", list(_ONISAGA_TYPES), ""),
            SourcePreference("pref_status", "Status Filter", "select", list(_ONISAGA_STATUSES), ""),
            SourcePreference("pref_rate_limit", "Image Requests Limit", "select", [
                ("1500", "1 image per 1.50 seconds"), ("1750", "1 image per 1.75 seconds"),
                ("2000", "1 image per 2.00 seconds"), ("2250", "1 image per 2.25 seconds"),
                ("2500", "1 image per 2.50 seconds"),
            ], "2000"),
        ]

    def get_filters(self) -> list[SourceFilter]:
        return [
            SourceFilter("platform", "Type", "select", list(_ONISAGA_TYPES), ""),
            SourceFilter("genre", "Genres", "tri_state", list(_ONISAGA_GENRES), []),
            SourceFilter("status", "Status", "select", list(_ONISAGA_STATUSES), ""),
            SourceFilter("min_chapters", "Min Chapters", "select", list(_ONISAGA_MIN_CHAPTERS), ""),
            SourceFilter("group", "Group", "text", default=""),
            SourceFilter("release_start", "Release Start Date (YYYY-MM-DD)", "text", default=""),
            SourceFilter("release_end", "Release End Date (YYYY-MM-DD)", "text", default=""),
            SourceFilter("sort", "Sort", "select", list(_ONISAGA_SORTS), "view"),
        ]

    async def browse(self, kind: str, page: int = 1):
        if kind not in {"popular", "latest"}:
            return {"items": [], "has_more": False}
        updates = self._updates(sort="view" if kind == "popular" else "created_at")
        return await self._livewire_page(f"{self.base_url}/browse", page, updates)

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        query = query.strip()
        url = f"{self.base_url}/search/{query}" if query else f"{self.base_url}/browse"
        return await self._livewire_page(url, page, self._updates_from_filters(filters or {}))

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request("GET", self._manga_url(series_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        return self._details(root, series_id)

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        url = self._manga_url(series_id)
        response = await self._request("GET", url)
        response.raise_for_status()
        root = _parse_html(response.text)
        self._strip_nsfw_overlay(root)
        state = self._livewire_state(root, "manga.chapter-list")
        if state is None:
            return []
        snapshot, token = state
        codes = [self.language_code] if self.language_code else list(_ONISAGA_ALL_LANGS)
        result: list[SourceChapter] = []
        for code in codes:
            result.extend(await self._chapters_for(url, snapshot, token, code, series_id))
        unique = list({chapter.source_id: chapter for chapter in result}.values())
        unique.sort(key=lambda chapter: chapter.number or 0.0, reverse=True)
        return unique

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else str(chapter)
        url = urljoin(f"{self.base_url}/", chapter_id.lstrip("/"))
        response = await self._request("GET", url)
        response.raise_for_status()
        found = _ONISAGA_READER_TOKEN.search(response.text)
        if not found:
            raise SourceNotFoundError(f"{self.display_name}: la pagina no trae readerToken")
        self._reader_token = found.group(1)
        count = len(_ONISAGA_PAGE_ORDER.findall(response.text))
        return [
            SourcePage(
                source_id=f"{chapter_id}#{index}",
                chapter_id=chapter_id,
                index=index,
                filename=f"{index}.jpg",
                source_name=self.name,
            )
            for index in range(count)
        ]

    async def page_bytes(self, page: SourcePage | str) -> SourcePageContent:
        value = page.source_id if isinstance(page, SourcePage) else str(page)
        chapter_id, _, order = value.rpartition("#")
        chapter_url = urljoin(f"{self.base_url}/", chapter_id.lstrip("/"))
        image_url = await self._image_url(chapter_url, order)
        response = await self._request(
            "GET", image_url, headers={"Referer": chapter_url},
        )
        response.raise_for_status()
        return SourcePageContent(
            media_type=response.headers.get("Content-Type", "image/jpeg"),
            chunks=iter([response.content]),
        )

    # -------------------------------------------------------------- livewire
    async def _livewire_page(self, url: str, page: int, updates: dict | None) -> dict:
        state = self._state if self._state and self._state[0] == url else None
        if state is None:
            response = await self._request("GET", url)
            response.raise_for_status()
            root = _parse_html(response.text)
            if page == 1 and updates is None:
                return self._manga_list(root)
            found = self._livewire_state(root, "post-filter")
            if found is None:
                raise SourceNotFoundError(f"{self.display_name}: sin estado Livewire")
            state = (url, found[0], found[1])
            self._state = state
        payload = await self._livewire_call(
            url,
            state[1],
            state[2],
            updates or self._updates(),
            [{"type": "call", "path": "", "method": "gotoPage", "params": [str(page)]}],
        )
        component = (payload.get("components") or [{}])[0]
        if component.get("snapshot"):
            self._state = (url, component["snapshot"], state[2])
        html = ((component.get("effects") or {}).get("html")) or ""
        return self._manga_list(_parse_html(html))

    async def _chapters_for(
        self, url: str, snapshot: str, token: str, code: str, series_id: str,
    ) -> list[SourceChapter]:
        current, previous, chapters = snapshot, 0, []
        while True:
            payload = await self._livewire_call(
                url, current, token, {"language": code},
                [{"type": "call", "path": "", "method": "loadMoreChapters", "params": []}],
            )
            component = (payload.get("components") or [{}])[0]
            html = ((component.get("effects") or {}).get("html")) or ""
            if not html:
                break
            chapters = self._chapters_from(
                _parse_html(html), code, self.language_code is None, series_id,
            )
            if len(chapters) <= previous:
                break
            previous = len(chapters)
            if not component.get("snapshot"):
                break
            current = component["snapshot"]
        return chapters

    async def _livewire_call(
        self, referer: str, snapshot: str, token: str, updates: dict, calls: list[dict],
    ) -> dict:
        response = await self._request(
            "POST",
            f"{self.base_url}/livewire/update",
            json={
                "_token": token,
                "components": [{"snapshot": snapshot, "updates": updates, "calls": calls}],
            },
            headers={
                "X-Livewire": "",
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
                "Origin": self.base_url,
                "Referer": referer.partition("?")[0],
            },
        )
        response.raise_for_status()
        return response.json() or {}

    @staticmethod
    def _livewire_state(root: _Node, component: str) -> tuple[str, str] | None:
        token = next(
            (
                node.attrs.get("content", "")
                for node in root.descendants("meta")
                if node.attrs.get("name") == "csrf-token" and node.attrs.get("content", "").strip()
            ),
            "",
        ) or next(
            (
                node.attrs.get("value", "")
                for node in root.descendants("input")
                if node.attrs.get("name") == "_token" and node.attrs.get("value", "").strip()
            ),
            "",
        )
        if not token:
            return None
        for node in root.descendants():
            for key, value in node.attrs.items():
                if key.endswith("snapshot") and component in value:
                    return value, token
        return None

    # --------------------------------------------------------------- parsing
    def _manga_list(self, root: _Node) -> dict:
        items: list[SourceSeries] = []
        for card in root.descendants("div"):
            if not (card.has_class("relative") and card.has_class("group")):
                continue
            entry = self._card(card)
            if entry is not None:
                items.append(entry)
        has_more = any(
            "nextPage" in value and "disabled" not in node.attrs
            for node in root.descendants()
            for key, value in node.attrs.items()
            if key == "wire:click"
        )
        return {"items": items, "has_more": has_more}

    def _card(self, card: _Node) -> SourceSeries | None:
        # La preferencia de contenido 18+ no vuelve a la fuente: se mantiene oculta.
        if _first(card, lambda node: node.tag == "span" and "18+" in node.text()) is not None:
            return None
        anchor = _first(
            card, lambda node: node.tag == "a" and "/manga/" in node.attrs.get("href", ""),
        )
        if anchor is None:
            return None
        parts = [part for part in urlparse(
            urljoin(f"{self.base_url}/", anchor.attrs.get("href", "")),
        ).path.split("/") if part]
        if len(parts) < 2 or parts[0].casefold() != "manga":
            return None
        heading = _first(
            card,
            lambda node: "data-flux-heading" in node.attrs or node.tag in {"h3", "h4"},
        ) or _first(card, lambda node: node.tag == "a" and node.attrs.get("title"))
        heading = heading or anchor
        title = heading.attrs.get("title", "").strip() or heading.text().strip()
        if not title:
            return None
        image = _first(
            card, lambda node: node.tag == "img" and node.attrs.get("alt", "").strip(),
        ) or _first(card, lambda node: node.tag == "img")
        return SourceSeries(
            source_id=parts[1],
            title=title,
            source_name=self.name,
            cover_url=self._image(image) if image is not None else None,
            web_url=f"{self.base_url}/manga/{parts[1]}",
        )

    def _details(self, root: _Node, series_id: str) -> SourceSeries:
        self._strip_nsfw_overlay(root)
        heading = _first(root, lambda node: node.tag == "h1") or _first(
            root, lambda node: "data-flux-heading" in node.attrs,
        )
        if heading is None:
            raise SourceNotFoundError(f"{self.display_name}: ficha sin titulo")
        badges = next(
            (
                node
                for node in root.descendants("div")
                if all(node.has_class(name) for name in
                       ("flex", "items-center", "gap-2", "justify-center", "mb-2"))
            ),
            None,
        )
        info = next(
            (
                node
                for node in root.descendants("div")
                if node.has_class("flex") and node.has_class("flex-col")
            ),
            None,
        )
        types = [
            text.capitalize()
            for node in (badges.descendants("div") if badges is not None else [])
            if "data-flux-badge" in node.attrs and (text := node.text().strip().casefold())
            in _ONISAGA_TYPE_BADGES
        ]
        tags = [
            text
            for node in (info.descendants("a") if info is not None else [])
            if "/genre/" in node.attrs.get("href", "") and (text := node.text().strip())
        ]
        summary = _first(root, lambda node: node.tag == "p" and node.has_class("leading-relaxed"))
        return SourceSeries(
            source_id=series_id,
            title=heading.text().strip(),
            source_name=self.name,
            cover_url=None,
            description=(summary.text().strip() if summary is not None else None) or None,
            author=", ".join(
                text
                for node in (info.descendants("a") if info is not None else [])
                if "/author/" in node.attrs.get("href", "") and (text := node.text().strip())
            ) or None,
            status=self._status(root),
            content_tags=tuple(types + tags),
            web_url=self._manga_url(series_id),
        )

    def _chapters_from(
        self, root: _Node, code: str, is_all: bool, series_id: str,
    ) -> list[SourceChapter]:
        result: list[SourceChapter] = []
        for anchor in root.descendants("a"):
            if not anchor.has_class("gap-4"):
                continue
            heading = _first(anchor, lambda node: "data-flux-heading" in node.attrs)
            number = self._chapter_number(anchor, heading)
            href = anchor.attrs.get("href", "")
            if number is None or "/read/" not in href:
                continue
            result.append(
                self._chapter(href, number, code if is_all else "", series_id, anchor),
            )
        for dropdown in root.descendants("ui-dropdown"):
            button = _first(dropdown, lambda node: node.tag == "button")
            if button is None:
                continue
            heading = _first(button, lambda node: "data-flux-heading" in node.attrs)
            number = self._chapter_number(button, heading)
            if number is None:
                continue
            unknown = 1
            for link in dropdown.descendants("a"):
                if "data-flux-menu-item" not in link.attrs:
                    continue
                href = link.attrs.get("href", "")
                if "/read/" not in href:
                    continue
                label = _first(link, lambda node: node.tag == "span" and node.has_class("text-sm"))
                group = label.text().strip() if label is not None else ""
                if not group or group.casefold() == "unknown group":
                    group = f"Unknown {unknown}"
                    unknown += 1
                result.append(
                    self._chapter(
                        href, number, f"{code} - {group}" if is_all else group, series_id, button,
                    )
                )
        return result

    def _chapter(
        self, href: str, number: str, scanlator: str, series_id: str, holder: _Node,
    ) -> SourceChapter:
        text = _first(holder, lambda node: node.tag == "p" and "data-flux-text" in node.attrs)
        details = [
            part
            for part in _ONISAGA_INTERPUNCT.split(
                (text.text().replace(" - ", " · ") if text is not None else ""),
            )
            if part
        ]
        stamp = next(
            (
                part
                for part in details
                if any(word in part.casefold() for word in ("ago", "today", "yesterday"))
            ),
            "",
        )
        return SourceChapter(
            source_id=urlparse(urljoin(f"{self.base_url}/", href)).path.lstrip("/"),
            title=f"Chapter {number}",
            series_id=series_id,
            source_name=self.name,
            number=self._float(number),
            language=self.language,
            scanlator=scanlator,
            uploaded_at=self._relative_date(stamp),
        )

    # -------------------------------------------------------------- internals
    async def _image_url(self, chapter_url: str, order: str) -> str:
        import asyncio
        import time

        identifier = chapter_url.rstrip("/").rsplit("/", 1)[-1]
        api = f"{self.base_url}/api/chapter/{identifier}/page/{order}"
        for _ in range(3):
            # El propio Kotlin espacia estas llamadas para no comerse un 429.
            wait = self.image_delay_seconds - (time.monotonic() - self._last_image_at)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_image_at = time.monotonic()
            response = await self._request(
                "GET",
                api,
                headers={
                    "X-Reader-Token": self._reader_token,
                    "Sec-Fetch-Mode": "cors",
                    "Sec-Fetch-Site": "same-origin",
                    "Referer": chapter_url,
                },
            )
            headers = getattr(response, "headers", None) or {}
            if headers.get("x-reader-token-next"):
                self._reader_token = headers["x-reader-token-next"]
            if getattr(response, "status_code", 200) == 429:
                continue
            payload = response.json() or {}
            if payload.get("url"):
                return str(payload["url"])
            refreshed = await self._request("GET", chapter_url)
            found = _ONISAGA_READER_TOKEN.search(refreshed.text)
            if not found:
                raise SourceNotFoundError(f"{self.display_name}: {payload.get('message')}")
            self._reader_token = found.group(1)
        raise SourceNotFoundError(f"{self.display_name}: sin imagen tras 3 intentos")

    def _updates(self, sort: str = "created_at") -> dict:
        return {
            "platform": "", "status": "", "sort": sort, "min_chapters": "",
            "group": None, "release_start": None, "release_end": None,
            "genre": [], "excludeGenre": [],
        }

    def _updates_from_filters(self, values: dict) -> dict | None:
        chosen = values.get("genre") or {}
        include = [key for key, state in chosen.items() if state == "include"] if isinstance(chosen, dict) else []
        exclude = [key for key, state in chosen.items() if state == "exclude"] if isinstance(chosen, dict) else []
        updates = {
            "platform": str(values.get("platform") or ""),
            "status": str(values.get("status") or ""),
            "sort": str(values.get("sort") or "created_at"),
            "min_chapters": str(values.get("min_chapters") or ""),
            "group": str(values["group"]).strip() or None if values.get("group") else None,
            "release_start": str(values["release_start"]).strip() or None if values.get("release_start") else None,
            "release_end": str(values["release_end"]).strip() or None if values.get("release_end") else None,
            "genre": include,
            "excludeGenre": exclude,
        }
        default = self._updates()
        return None if updates == default else updates

    def _manga_url(self, series_id: str) -> str:
        slug = series_id.rstrip("/").rsplit("/", 1)[-1]
        return f"{self.base_url}/manga/{slug}"

    def _image(self, node: _Node) -> str | None:
        value = (
            node.attrs.get("data-src")
            or node.attrs.get("data-lazy-src")
            or node.attrs.get("src")
            or ""
        )
        if not value or value.startswith("data:"):
            return None
        return urljoin(f"{self.base_url}/", value)

    @staticmethod
    def _strip_nsfw_overlay(root: _Node) -> None:
        marker = _first(root, lambda node: node.tag == "span" and "18+" in node.text())
        if marker is None:
            return
        overlay = _onisaga_ancestor(marker, "absolute", "inset-0", "z-20")
        if overlay is not None:
            _onisaga_detach(overlay)

    @staticmethod
    def _chapter_number(holder: _Node, heading: _Node | None) -> str | None:
        if heading is not None:
            text = heading.text().replace("Chapter ", "").strip()
            if text:
                return text
        fallback = _first(holder, lambda node: node.has_class("w-10"))
        return fallback.text().strip() if fallback is not None else None

    @staticmethod
    def _status(root: _Node) -> str | None:
        marker = _first(
            root,
            lambda node: node.tag == "span"
            and any(
                child.tag == "span" and child.has_class("size-1.5")
                for child in node.children
                if isinstance(child, _Node)
            ),
        )
        text = marker.text().casefold() if marker is not None else ""
        if not text:
            candidate = _first(
                root,
                lambda node: node.tag == "span"
                and node.has_class("inline-flex")
                and any(
                    word in node.text()
                    for word in ("Completed", "Ongoing", "Hiatus", "Cancelled")
                ),
            )
            text = candidate.text().casefold() if candidate is not None else ""
        for words, value in (
            (("ongoing", "releasing"), "ongoing"),
            (("completed",), "completed"),
            (("hiatus",), "hiatus"),
            (("cancelled", "dropped"), "cancelled"),
        ):
            if any(word in text for word in words):
                return value
        return None

    @staticmethod
    def _float(value: str) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _relative_date(value: str) -> str | None:
        from datetime import datetime, timedelta

        text = value.casefold()
        now = datetime.now().replace(microsecond=0)
        if not text:
            return None
        if "today" in text:
            return now.isoformat()
        if "yesterday" in text:
            return (now - timedelta(days=1)).isoformat()
        found = _ONISAGA_RELATIVE.search(text)
        if not found:
            return None
        amount, unit = int(found.group(1)), found.group(2)
        spans = {
            "minute": timedelta(minutes=1), "hour": timedelta(hours=1), "day": timedelta(days=1),
            "week": timedelta(weeks=1), "month": timedelta(days=30), "year": timedelta(days=365),
        }
        return (now - spans[unit] * amount).isoformat()




SOURCE = OnisagaSource
