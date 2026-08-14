try:
    from .base import FuenteBaseSource, _Node, _TreeParser
except ImportError:
    pass

class FuenteBaseSource:
    pass


class NamicomiSource(FuenteBaseSource):
    """API tipo MangaDex: relaciones incluidas y capitulos con control de acceso."""

    @property
    def ext_language(self) -> str:
        return _NAMICOMI_EXT_LANGS.get(self.language, self.language)

    def get_preferences(self) -> list[SourcePreference]:
        return [
            SourcePreference(
                f"thumbnailQuality_{self.ext_language}", "Cover quality", "select",
                [("", "Original"), (".512.jpg", "Medium"), (".256.jpg", "Low")], "",
            ),
            SourcePreference(f"dataSaver_{self.ext_language}", "Data saver", "checkbox", default=False),
            SourcePreference(
                f"showLockedChapters_{self.ext_language}", "Show locked chapters",
                "checkbox", default=False,
            ),
        ]

    def get_filters(self) -> list[SourceFilter]:
        return [
            SourceFilter("hasAvailableChapters", "Has available chapters", "checkbox", default=False),
            SourceFilter("contentRatings", "Content rating", "multi_select", list(_NAMICOMI_RATINGS), []),
            SourceFilter("publicationStatuses", "Status", "multi_select", list(_NAMICOMI_STATUSES), []),
            SourceFilter("sort", "Sort", "select", list(_NAMICOMI_SORTS), "publishedAt"),
            SourceFilter("sortDirection", "Sort direction", "select", [
                ("desc", "Descending"), ("asc", "Ascending"),
            ], "desc"),
            SourceFilter("includedTagsMode", "Included tags mode", "select", [
                ("and", "And"), ("or", "Or"),
            ], "and"),
            SourceFilter("excludedTagsMode", "Excluded tags mode", "select", [
                ("and", "And"), ("or", "Or"),
            ], "or"),
            *[
                SourceFilter(identifier, label, "tri_state", list(options), [])
                for identifier, label, options in _NAMICOMI_TAG_FILTERS
            ],
        ]

    async def browse(self, kind: str, page: int = 1):
        if kind not in {"popular", "latest"}:
            return {"items": [], "has_more": False}
        params = [
            (f"order[{'views' if kind == 'popular' else 'publishedAt'}]", "desc"),
            ("availableTranslatedLanguages[]", self.ext_language),
            ("limit", str(_NAMICOMI_LIMIT)),
            ("offset", str(_NAMICOMI_LIMIT * (page - 1))),
            *self._includes(),
        ]
        return self._manga_list(await self._get(f"{_NAMICOMI_API}/title/search", params))

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        query = query.strip()
        if query.startswith("https://"):
            parts = [part for part in urlparse(query).path.split("/") if part]
            if urlparse(query).netloc != urlparse(self.base_url).netloc or len(parts) < 3:
                raise ValueError("URL no compatible")
            query = f"id:{parts[2]}"
        if query.startswith("id:"):
            identifier = query[3:].strip()
            if not identifier:
                raise ValueError("Identificador invalido")
            return self._manga_list(
                await self._get(
                    f"{_NAMICOMI_API}/title/search", [("ids[]", identifier), *self._includes()],
                )
            )
        values = filters or {}
        params: list[tuple[str, str]] = [
            ("limit", str(_NAMICOMI_LIMIT)),
            ("offset", str(_NAMICOMI_LIMIT * (page - 1))),
            *self._includes(),
        ]
        normalized = " ".join(query.split())
        if normalized:
            params.append(("title", normalized))
        if values.get("hasAvailableChapters"):
            params.append(("hasAvailableChapters", "true"))
            params.append(("availableTranslatedLanguages[]", self.ext_language))
        params.extend(
            ("contentRatings[]", str(value)) for value in values.get("contentRatings") or []
        )
        params.extend(
            ("publicationStatuses[]", str(value))
            for value in values.get("publicationStatuses") or []
        )
        params.append((
            f"order[{values.get('sort') or 'publishedAt'}]",
            "asc" if str(values.get("sortDirection")) == "asc" else "desc",
        ))
        params.append(("includedTagsMode", str(values.get("includedTagsMode") or "and")))
        params.append(("excludedTagsMode", str(values.get("excludedTagsMode") or "or")))
        for identifier, _, _ in _NAMICOMI_TAG_FILTERS:
            chosen = values.get(identifier)
            if not isinstance(chosen, dict):
                continue
            params.extend(
                ("includedTags[]" if state == "include" else "excludedTags[]", str(tag))
                for tag, state in chosen.items()
                if state in {"include", "exclude"}
            )
        return self._manga_list(await self._get(f"{_NAMICOMI_API}/title/search", params))

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        payload = await self._get(f"{_NAMICOMI_API}/title/{series_id}", list(self._includes()))
        data = payload.get("data")
        if not isinstance(data, dict):
            raise SourceNotFoundError(f"{self.display_name}: ficha no encontrada")
        return self._manga(data)

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        entries: list[dict] = []
        offset = 0
        while True:
            payload = await self._get(f"{_NAMICOMI_API}/chapter", [
                ("titleId", series_id),
                ("includes[]", "organization"),
                ("limit", "200"),
                ("offset", str(offset)),
                ("translatedLanguages[]", self.ext_language),
                ("order[volume]", "desc"),
                ("order[chapter]", "desc"),
            ])
            entries.extend(
                item for item in payload.get("data") or [] if isinstance(item, dict)
            )
            meta = payload.get("meta") or {}
            limit, current, total = (
                int(meta.get("limit") or 0), int(meta.get("offset") or 0), int(meta.get("total") or 0),
            )
            if limit + current >= total or not limit:
                break
            offset = current + limit
        if not entries:
            return []
        # El acceso a cada capitulo se consulta aparte, en tandas de 200.
        access: dict[str, bool] = {}
        identifiers = [str(item.get("id")) for item in entries]
        for start in range(0, len(identifiers), 200):
            response = await self._request(
                "POST",
                f"{_NAMICOMI_API}/gating/check",
                json={
                    "entities": [
                        {"entityId": value, "entityType": "chapter"}
                        for value in identifiers[start : start + 200]
                    ],
                },
            )
            response.raise_for_status()
            payload = response.json() or {}
            data = payload.get("data")
            if isinstance(data, dict):
                access.update((data.get("attributes") or {}).get("map") or {})
        result: list[SourceChapter] = []
        for item in entries:
            identifier = str(item.get("id"))
            if not access.get(identifier):
                continue
            result.append(self._chapter(item, series_id))
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else str(chapter)
        payload = await self._get(
            f"{_NAMICOMI_API}/images/chapter/{chapter_id}", [("newQualities", "true")],
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            return []
        prefix = f"{data.get('baseUrl')}/chapter/{chapter_id}/{data.get('hash')}"
        images = data.get("source") or []
        return [
            SourcePage(
                source_id=f"{prefix}/source/{image.get('filename')}",
                chapter_id=chapter_id,
                index=index,
                filename=str(image.get("filename") or f"{index}.jpg"),
                source_name=self.name,
            )
            for index, image in enumerate(images)
            if isinstance(image, dict) and image.get("filename")
        ]

    # -------------------------------------------------------------- internals
    async def _get(self, url: str, params: list[tuple[str, str]]) -> dict:
        response = await self._request("GET", url, params=params)
        if getattr(response, "status_code", 200) == 204:
            return {"data": [], "meta": {}}
        response.raise_for_status()
        return response.json() or {}

    @staticmethod
    def _includes() -> list[tuple[str, str]]:
        return [("includes[]", value) for value in _NAMICOMI_INCLUDES]

    def _manga_list(self, payload: dict) -> dict:
        meta = payload.get("meta") or {}
        limit, offset, total = (
            int(meta.get("limit") or 0), int(meta.get("offset") or 0), int(meta.get("total") or 0),
        )
        return {
            "items": [
                self._manga(item)
                for item in payload.get("data") or []
                if isinstance(item, dict)
            ],
            "has_more": limit + offset < total,
        }

    def _manga(self, item: dict) -> SourceSeries:
        attributes = item.get("attributes") or {}
        relationships = [
            value for value in item.get("relationships") or [] if isinstance(value, dict)
        ]
        titles = attributes.get("title") or {}
        title = titles.get(self.ext_language) or next(iter(titles.values()), "")
        descriptions = attributes.get("description") or {}
        organizations = list(dict.fromkeys(
            str((value.get("attributes") or {}).get("name"))
            for value in relationships
            if value.get("type") == "organization" and (value.get("attributes") or {}).get("name")
        ))
        cover = next(
            (
                (value.get("attributes") or {}).get("fileName")
                for value in relationships
                if value.get("type") == "cover_art" and (value.get("attributes") or {}).get("fileName")
            ),
            None,
        )
        grouped: dict[str, list[str]] = {}
        for value in relationships:
            if value.get("type") not in {"tag", "primary_tag", "secondary_tag"}:
                continue
            group = (value.get("attributes") or {}).get("group")
            name = _NAMICOMI_TAG_NAMES.get(str(value.get("id")))
            if group and name:
                grouped.setdefault(str(group), []).append(name)
        tags = [name for group in _NAMICOMI_TAG_GROUPS for name in sorted(grouped.get(group, []))]
        rating = attributes.get("contentRating")
        if rating and rating != "safe":
            tags.append(f"Content rating: {rating.capitalize()}")
        original = attributes.get("originalLanguage")
        if original:
            tags.append(_NAMICOMI_LANG_NAMES.get(original, str(original).upper()))
        return SourceSeries(
            source_id=str(item.get("id")),
            title=str(title),
            source_name=self.name,
            cover_url=f"{_NAMICOMI_CDN}/covers/{item.get('id')}/{cover}" if cover else None,
            description=str(
                descriptions.get(self.ext_language) or descriptions.get("en") or "",
            ) or None,
            author=", ".join(organizations) or None,
            status=self._status(attributes.get("publicationStatus")),
            content_tags=tuple(value for value in tags if value),
            web_url=(
                f"{self.base_url}/{self.ext_language}/title/{item.get('id')}"
                f"/{self._slug(str(title))}"
            ),
        )

    def _chapter(self, item: dict, series_id: str) -> SourceChapter:
        attributes = item.get("attributes") or {}
        parts: list[str] = []
        if attributes.get("volume"):
            parts.append(f"Vol.{attributes['volume']}")
        if attributes.get("chapter"):
            parts.append(f"Ch.{attributes['chapter']}")
        if attributes.get("name"):
            if parts:
                parts.append("-")
            parts.append(str(attributes["name"]))
        return SourceChapter(
            source_id=str(item.get("id")),
            title=" ".join(parts),
            series_id=series_id,
            source_name=self.name,
            number=self._float(attributes.get("chapter")),
            language=self.language,
            scanlator=", ".join(
                str((value.get("attributes") or {}).get("name"))
                for value in item.get("relationships") or []
                if isinstance(value, dict) and value.get("type") == "organization"
                and (value.get("attributes") or {}).get("name")
            ),
            uploaded_at=self._date(attributes.get("publishAt")),
        )

    @staticmethod
    def _slug(title: str) -> str:
        cleaned = re.sub(r"[^a-z0-9]+", "-", title.strip().casefold())
        cleaned = re.sub(r"-+$", "", cleaned)
        result = ""
        for part in cleaned.split("-"):
            candidate = f"{result}-{part}" if result else part
            if result and len(candidate) > 100:
                break
            result = candidate
        return result

    @staticmethod
    def _status(value: Any) -> str | None:
        return {
            "ongoing": "ongoing", "completed": "completed",
            "hiatus": "hiatus", "cancelled": "cancelled",
        }.get(str(value or ""))

    @staticmethod
    def _float(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _date(value: Any) -> str | None:
        from datetime import datetime

        if not value:
            return None
        text = str(value)
        for pattern in ("%Y-%m-%dT%H:%M:%S+%f", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(text, pattern).replace(microsecond=0).isoformat()
            except ValueError:
                continue
        return None


class GeneratedNamiComiSource(NamiComiSource):
    name = 'namicomi_es'
    display_name = 'NamiComi'
    base_url = 'https://namicomi.com'
    language = 'es'
    requests_per_minute = 180
    content_warning = 'safe'
    image_headers = {'Referer': 'https://namicomi.com/'}


SOURCE = NamicomiSource
