try:
    from .madara import (
        MadaraSource, _Node, _TreeParser
    )
except ImportError:
    pass

class MadaraSource:
    pass


_YELLOWNOTE_STRINGS = {
    "en": {
        "filter.sort.title": "Sort by",
        "filter.sort.option.last-update": "Last Update",
        "filter.sort.option.popularity": "Popularity",
        "filter.sort.option.most-comments": "Comment Count",
        "filter.sort.option.latest-comments": "Latest Comments",
        "filter.category.title": "Category",
        "filter.category.option.theme.xiuren-featured": "Theme: Xiuren Featured",
        "filter.category.option.theme.large-scale": "Theme: Large Scale",
        "filter.category.option.theme.sex": "Theme: Sex",
        "filter.category.option.theme.exposure": "Theme: Exposure",
        "filter.category.option.theme.cosplay": "Theme: Cosplay",
        "filter.category.option.theme.sex-toy": "Theme: Sex Toy",
        "filter.category.option.theme.bondage": "Theme: Bondage",
        "filter.category.option.theme.shaved-pussy": "Theme: Shaved Pussy",
        "filter.category.option.theme.lesbian": "Theme: Lesbian",
        "filter.category.option.theme.with-original-photos": "Theme: With Original Photos",
        "filter.category.option.theme.with-video": "Theme: With Video(s)",
        "filter.category.option.theme.amateur": "Theme: Amateur",
        "config.image_quality.title": "Image Quality",
    },
    "es": {
        "filter.sort.title": "Ordenar por",
        "filter.sort.option.last-update": "Última actualización",
        "filter.sort.option.popularity": "Contenido más popular",
        "filter.sort.option.most-comments": "Más comentarios",
        "filter.sort.option.latest-comments": "Comentarios más recientes",
        "filter.category.title": "Categoría del álbum",
        "filter.category.option.theme.xiuren-featured": "Xiuren Gran escala",
        "filter.category.option.theme.large-scale": "Gran Escala",
        "filter.category.option.theme.sex": "Sexo",
        "filter.category.option.theme.exposure": "Exposición",
        "filter.category.option.theme.cosplay": "Cosplay",
        "filter.category.option.theme.sex-toy": "Juguete Sexual",
        "filter.category.option.theme.bondage": "Esclavitud",
        "filter.category.option.theme.shaved-pussy": "Coño Afeitado",
        "filter.category.option.theme.lesbian": "Lesbiana",
        "filter.category.option.theme.with-original-photos": "Con fotos originales",
        "filter.category.option.theme.with-video": "Con vídeo(s)",
        "filter.category.option.theme.amateur": "Aficionado",
        "config.image_quality.title": "Calidad de imagen",
    },
}
_YELLOWNOTE_CATEGORIES = (
    ("photos/album-1", "filter.category.option.theme.xiuren-featured"),
    ("photos/album-2", "filter.category.option.theme.large-scale"),
    ("photos/album-3", "filter.category.option.theme.sex"),
    ("photos/album-4", "filter.category.option.theme.exposure"),
    ("photos/album-5", "filter.category.option.theme.cosplay"),
    ("photos/album-6", "filter.category.option.theme.sex-toy"),
    ("photos/album-7", "filter.category.option.theme.bondage"),
    ("photos/album-8", "filter.category.option.theme.shaved-pussy"),
    ("photos/album-9", "filter.category.option.theme.lesbian"),
    ("photos/album-10", "filter.category.option.theme.with-original-photos"),
    ("photos/album-11", "filter.category.option.theme.with-video"),
    ("amateurs", "filter.category.option.theme.amateur"),
    ("photos/series-637b2029d2347", "filter.category.option.taiwan-studios-jvid"),
    ("photos/series-5f889afb37619", "filter.category.option.taiwan-studios-fantasy-factory"),
    ("photos/series-5f7a0a80d3d66", "filter.category.option.taiwan-studios-tpimage"),
    ("photos/series-6310ce9b90056", "filter.category.option.chinese-studios-pans"),
    ("photos/series-6666a7ac3ba9c", "filter.category.option.chinese-studios-wind-sings"),
    ("photos/series-64f44d99ce673", "filter.category.option.chinese-studios-xing-se"),
    ("photos/series-665f8bafab4bc", "filter.category.option.chinese-studios-huang-fu"),
    ("photos/series-665f7d787d681", "filter.category.option.chinese-studios-other-studios"),
    ("photos/series-5f1dcdeaee582", "filter.category.option.chinese-studios-metcn"),
    ("photos/series-5f1d784995865", "filter.category.option.chinese-studios-litu"),
    ("photos/series-638e5a60b1770", "filter.category.option.chinese-studios-midnight-project"),
    ("photos/series-5f23c44cd66bd", "filter.category.option.chinese-studios-pandora"),
    ("photos/series-5f2089564c6c2", "filter.category.option.chinese-studios-missleg"),
    ("photos/series-646c69b675f3d", "filter.category.option.chinese-studios-iss"),
    ("photos/series-5f15f389e993e", "filter.category.option.chinese-studios-aiss"),
    ("photos/series-5f60b98248a81", "filter.category.option.chinese-studios-au"),
    ("photos/series-622c7f95220a4", "filter.category.option.chinese-studios-beijing-angel"),
    ("photos/series-619a92aa1fa7a", "filter.category.option.chinese-studios-wuji-works"),
    ("photos/series-676c3e9b90749", "filter.category.option.chinese-studios-pomelo"),
    ("photos/series-5f382ba894af4", "filter.category.option.chinese-studios-sk-silk"),
    ("photos/series-5f15f727df393", "filter.category.option.chinese-studios-ddy"),
    ("photos/series-5f22ea422221c", "filter.category.option.chinese-studios-dongguan-vgirls"),
    ("photos/series-61b997728043b", "filter.category.option.chinese-studios-youmei"),
    ("photos/series-6443d480eb757", "filter.category.option.others-ai-photos"),
    ("photos/series-665f81885f103", "filter.category.option.korean-studios-makemodel"),
    ("photos/series-6224e755e21f4", "filter.category.option.korean-studios-pure-media"),
    ("photos/series-665a2385a2367", "filter.category.option.korean-studios-espacia-korea"),
    ("photos/series-62888afad416b", "filter.category.option.korean-studios-loozy"),
    ("photos/series-6450b47c9db0b", "filter.category.option.japanese-studios-graphis"),
    ("photos/series-66f9665804471", "filter.category.option.japanese-studios-kuni-scan"),
    ("photos/series-66e68b9c96ab0", "filter.category.option.japanese-studios-weekly-post-digital-photo"),
    ("photos/series-670d7142b3d88", "filter.category.option.japanese-studios-morning-sexy"),
    ("photos/series-670791f5f2f0f", "filter.category.option.japanese-studios-prestige"),
    ("photos/series-66fb8cca706ae", "filter.category.option.japanese-studios-x-city"),
    ("photos/series-66659e2d94489", "filter.category.option.japanese-studios-friday"),
    ("photos/series-63e7481fa2c44", "filter.category.option.japanese-studios-flash"),
    ("photos/series-662da0561effa", "filter.category.option.japanese-studios-ex-max"),
    ("photos/series-64637db850548", "filter.category.option.japanese-studios-young-magazine"),
    ("photos/series-66ec3705efc8b", "filter.category.option.japanese-studios-young-gangan"),
    ("photos/series-6562a4a15c6d1", "filter.category.option.japanese-studios-weekly-playboy"),
    ("photos/series-645739068c9e5", "filter.category.option.japanese-studios-super-pose-book"),
    ("photos/series-6288877690068", "filter.category.option.japanese-studios-urabon"),
    ("photos/series-670d6bf875331", "filter.category.option.japanese-studios-escape"),
)
_YELLOWNOTE_SORT = (
    ("", "filter.sort.option.last-update"),
    ("sort-hot", "filter.sort.option.popularity"),
    ("sort-comment", "filter.sort.option.most-comments"),
    ("sort-recent", "filter.sort.option.latest-comments"),
)
_YELLOWNOTE_STYLE_URL = re.compile(r"background-image\s*:\s*url\('([^']+)'\)")
_YELLOWNOTE_MEDIA_COUNT = re.compile(r"^\d+P( \+ \d+V)?$")
_YELLOWNOTE_DATE = re.compile(r"\d{4}\.\d{2}\.\d{2}")


def _yellownote_kids(node: _Node, tag: str, class_name: str | None = None) -> list[_Node]:
    return [
        child
        for child in node.children
        if isinstance(child, _Node)
        and child.tag == tag
        and (class_name is None or child.has_class(class_name))
    ]


def _yellownote_classes(node: _Node, *names: str) -> bool:
    return all(node.has_class(name) for name in names)


class YellownoteSource(MadaraSource):
    """Albumes de fotos: cada pagina del album es un capitulo."""

    def _text(self, key: str) -> str:
        strings = _YELLOWNOTE_STRINGS.get(self.language) or {}
        return strings.get(key) or _YELLOWNOTE_STRINGS["en"].get(key, key)

    def get_preferences(self) -> list[SourcePreference]:
        return [
            SourcePreference(
                id="XChina::IMAGE_QUALITY",
                name=self._text("config.image_quality.title"),
                type="select",
                options=[("original", "原图(JPG)"), ("webp_hd", "高清(WebP)")],
                default="original",
            )
        ]

    def get_filters(self) -> list[SourceFilter]:
        return [
            SourceFilter(
                "sort",
                self._text("filter.sort.title"),
                "select",
                [(value, self._text(key)) for value, key in _YELLOWNOTE_SORT],
                "",
            ),
            SourceFilter(
                "category",
                self._text("filter.category.title"),
                "select",
                [(value, self._text(key)) for value, key in _YELLOWNOTE_CATEGORIES],
                _YELLOWNOTE_CATEGORIES[0][0],
            ),
        ]

    async def browse(self, kind: str, page: int = 1):
        if kind == "popular":
            return await self._listing(f"{self.base_url}/photos/sort-hot/{page}.html")
        if kind == "latest":
            return await self._listing(f"{self.base_url}/photos/{page}.html")
        return {"items": [], "has_more": False}

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        values = filters or {}
        query = query.strip()
        if query:
            # Una busqueda por texto ignora la categoria, como avisa el propio filtro.
            part = f"photos/keyword-{query}"
        else:
            part = str(values.get("category") or _YELLOWNOTE_CATEGORIES[0][0])
        segments = [part]
        sort = str(values.get("sort") or "")
        if sort.strip():
            segments.append(sort)
        segments.append(f"{page}.html")
        return await self._listing(f"{self.base_url}/{'/'.join(segments)}")

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        card = self._card(root)
        if card is None:
            raise SourceNotFoundError(f"{self.display_name}: ficha sin tarjeta de informacion")
        name = self._by_icon(card, "fa-address-card")
        media = self._by_icon(card, "fa-image")
        if name is None or media is None:
            raise SourceNotFoundError(f"{self.display_name}: ficha incompleta")
        number = self._by_icon(card, "fa-file")
        floating = next(
            (
                node
                for node in card.descendants("div")
                if _yellownote_classes(node, "item", "floating")
            ),
            None,
        )
        tags = [
            value
            for key, skip_dash in (("fa-video-camera", True), ("fa-filter", False), ("fa-tags", False))
            for value in (self._list_by_icon(card, key) or [])
            if not (skip_dash and value == "-")
        ]
        known = series if isinstance(series, SourceSeries) else None
        return SourceSeries(
            source_id=series_id,
            title=f"{name}{f' {number}' if number else ''}({media})",
            source_name=self.name,
            cover_url=known.cover_url if known else None,
            author=(
                floating.text().strip() if floating is not None
                else self._by_icon(card, "fa-circle-user")
            ) or None,
            status="completed",
            content_tags=tuple(tags),
            web_url=urljoin(f"{self.base_url}/", series_id),
        )

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        card = self._card(root)
        stamp = self._date(self._by_icon(card, "fa-calendar-days") if card is not None else None)
        if stamp is None:
            stamp = self._version_date(root)
        numbers = [
            value
            for anchor in self._pager_anchors(root, "pager-num")
            if (value := self._int(anchor.text().strip())) is not None
        ]
        last = numbers[-1] if numbers else 1
        base = series_id[:-5] if series_id.endswith(".html") else series_id
        return [
            SourceChapter(
                source_id=f"{base}/{page}.html",
                title=f"Page {page}",
                series_id=series_id,
                source_name=self.name,
                number=float(page),
                language=self.language,
                uploaded_at=stamp,
            )
            for page in range(last, 0, -1)
        ]

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else str(chapter)
        response = await self._request("GET", urljoin(f"{self.base_url}/", chapter_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        urls: list[str] = []
        for holder in root.descendants("div"):
            if not (
                _yellownote_classes(holder, "list", "photo-items")
                or _yellownote_classes(holder, "list", "amateur-items")
            ):
                continue
            for item in _yellownote_kids(holder, "div"):
                if not (
                    _yellownote_classes(item, "item", "photo-image")
                    or _yellownote_classes(item, "item", "amateur-image")
                ):
                    continue
                value = self._style_url(item)
                if not value:
                    continue
                # La calidad "original" (por defecto) pide el JPG en vez del WebP.
                if "_600x0.webp" in value:
                    value = value.replace("_600x0.webp", ".jpg")
                urls.append(value)
        return [
            SourcePage(
                source_id=value,
                chapter_id=chapter_id,
                index=index,
                filename=urlparse(value).path.rsplit("/", 1)[-1] or f"{index}.jpg",
                source_name=self.name,
            )
            for index, value in enumerate(urls)
        ]

    async def _listing(self, url: str) -> dict:
        response = await self._request("GET", url)
        response.raise_for_status()
        root = _parse_html(response.text)
        base = str(response.url) or url
        items: list[SourceSeries] = []
        for holder in root.descendants("div"):
            if not (
                _yellownote_classes(holder, "list", "photo-list")
                or _yellownote_classes(holder, "list", "amateur-list")
            ):
                continue
            for item in _yellownote_kids(holder, "div"):
                if not (
                    _yellownote_classes(item, "item", "photo")
                    or _yellownote_classes(item, "item", "amateur")
                ):
                    continue
                anchor = _first(item, lambda node: node.tag == "a")
                if anchor is None:
                    continue
                href, title = anchor.attrs.get("href", ""), anchor.attrs.get("title", "").strip()
                if not href.strip() or not title:
                    continue
                count = next(
                    (
                        text
                        for tags in item.descendants("div")
                        if tags.has_class("tags")
                        for node in _yellownote_kids(tags, "div")
                        if _YELLOWNOTE_MEDIA_COUNT.match(text := node.text().strip())
                    ),
                    "",
                )
                items.append(
                    SourceSeries(
                        source_id=urlparse(urljoin(base, href)).path.lstrip("/"),
                        title=f"{title}({count})" if count else title,
                        source_name=self.name,
                        cover_url=self._style_url(anchor) or None,
                        web_url=urljoin(base, href),
                    )
                )
        return {"items": items, "has_more": bool(self._pager_anchors(root, "pager-next"))}

    @staticmethod
    def _card(root: _Node) -> _Node | None:
        return next(
            (
                node
                for node in root.descendants("div")
                if _yellownote_classes(node, "info-card", "photo-detail")
            ),
            None,
        )

    @staticmethod
    def _pager_anchors(root: _Node, class_name: str) -> list[_Node]:
        pager = next((node for node in root.descendants("div") if node.has_class("pager")), None)
        if pager is None:
            return []
        return [node for node in pager.descendants("a") if node.has_class(class_name)]

    @staticmethod
    def _style_url(node: _Node) -> str:
        image = next(
            (child for child in node.descendants("div") if child.has_class("img")), None,
        )
        if image is None:
            return ""
        found = _YELLOWNOTE_STYLE_URL.search(image.attrs.get("style", ""))
        return found.group(1) if found else ""

    @classmethod
    def _item_by_icon(cls, card: _Node, icon: str) -> _Node | None:
        for item in card.descendants("div"):
            if not item.has_class("item"):
                continue
            marker = next(
                (
                    node
                    for holder in item.descendants()
                    if holder.has_class("icon")
                    for node in _yellownote_kids(holder, "i")
                    if node.has_class(icon)
                ),
                None,
            )
            if marker is not None:
                return next(
                    (node for node in item.descendants("div") if node.has_class("text")), None,
                )
        return None

    @classmethod
    def _by_icon(cls, card: _Node, icon: str) -> str | None:
        node = cls._item_by_icon(card, icon)
        return node.text().strip() if node is not None else None

    @classmethod
    def _list_by_icon(cls, card: _Node, icon: str) -> list[str] | None:
        node = cls._item_by_icon(card, icon)
        if node is None:
            return None
        return [
            child.text().strip()
            for child in node.children
            if isinstance(child, _Node)
        ]

    @classmethod
    def _version_date(cls, root: _Node) -> str | None:
        for holder in root.descendants("div"):
            if not holder.has_class("tab-content"):
                continue
            for card in holder.descendants("div"):
                if not card.has_class("info-card"):
                    continue
                for node in card.descendants("div"):
                    if node.has_class("text") and (stamp := cls._date(node.text())):
                        return stamp
        return None

    @staticmethod
    def _int(value: str) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _date(value: str | None) -> str | None:
        from datetime import datetime

        found = _YELLOWNOTE_DATE.search(value or "")
        if not found:
            return None
        try:
            return datetime.strptime(found.group(), "%Y.%m.%d").isoformat()
        except ValueError:
            return None




SOURCE = YellownoteSource
