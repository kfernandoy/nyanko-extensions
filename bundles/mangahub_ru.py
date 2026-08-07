"""Implementación común del tema Madara para bundles Nyanko Source v4."""

from __future__ import annotations

import ast
import base64
import io
import json
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlparse, urlunparse

from PIL import Image

from nyanko_api.sources.contract import (
    SOURCE_API_VERSION,
    SourceCapabilities,
    SourceChapter,
    SourceFetcher,
    SourceFilter,
    SourcePage,
    SourcePageContent,
    SourcePreference,
    SourceSeries,
)
from nyanko_api.sources.errors import SourceNotFoundError


class _Node:
    def __init__(
        self,
        tag: str = "",
        attrs: list[tuple[str, str | None]] | None = None,
        parent: _Node | None = None,
    ) -> None:
        self.tag = tag
        self.attrs = {key: value or "" for key, value in attrs or []}
        self.parent = parent
        self.children: list[_Node | str] = []

    def text(self) -> str:
        return " ".join(
            part
            for child in self.children
            if (part := child.text() if isinstance(child, _Node) else child.strip())
        )

    def descendants(self, tag: str | None = None) -> list[_Node]:
        result: list[_Node] = []
        for child in self.children:
            if not isinstance(child, _Node):
                continue
            if tag is None or child.tag == tag:
                result.append(child)
            result.extend(child.descendants(tag))
        return result

    def has_class(self, name: str) -> bool:
        return name in self.attrs.get("class", "").split()


class _TreeParser(HTMLParser):
    _VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node()
        self.current = self.root

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag, attrs, self.current)
        self.current.children.append(node)
        if tag not in self._VOID:
            self.current = node

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.current.children.append(_Node(tag, attrs, self.current))

    def handle_endtag(self, tag: str) -> None:
        node = self.current
        while node.parent is not None:
            if node.tag == tag:
                self.current = node.parent
                return
            node = node.parent

    def handle_data(self, data: str) -> None:
        self.current.children.append(data)


def _parse_html(value: str) -> _Node:
    parser = _TreeParser()
    parser.feed(value)
    return parser.root


def _first(node: _Node, predicate: Any) -> _Node | None:
    return next((item for item in node.descendants() if predicate(item)), None)


_BACKGROUND_IMAGE = re.compile(r"background(?:-image)?\s*:[^;]*?url\(\s*(['\"]?)(.*?)\1\s*\)", re.I | re.S)


def _style_image_url(node: _Node, base_url: str) -> str:
    """Portada servida como CSS en el propio nodo, no como <img>.

    Los temas Madara re-skineados con Tailwind pintan la portada con
    ``style="background-image:url(...)"`` sobre el ancla de la serie y no
    emiten ni un solo ``<img>``.
    """
    found = _BACKGROUND_IMAGE.search(node.attrs.get("style", ""))
    if found is None:
        return ""
    value = found.group(2).strip()
    return urljoin(base_url, value) if value else ""


def _image_url(node: _Node, base_url: str) -> str:
    for key in (
        "data-lm-orig-src",
        "data-sec-src",
        "data-src",
        "data-lazy-src",
        "data-cfsrc",
        "data-manga-src",
        "data-src-base64",
        "src",
    ):
        if node.attrs.get(key):
            return urljoin(base_url, node.attrs[key].strip())
    candidates = [
        item.strip().split()[0]
        for item in node.attrs.get("srcset", "").split(",")
        if item.strip()
    ]
    if candidates:
        return urljoin(base_url, candidates[-1])
    return _style_image_url(node, base_url)


def _cover_url(container: _Node, base_url: str) -> str | None:
    """Portada del contenedor: primero el <img>, si no el background del CSS.

    Es aditivo: el fallback de ``background-image`` solo entra cuando no hay
    ningun ``<img>`` con URL utilizable, asi que no puede cambiar el resultado
    de los sitios que hoy funcionan.
    """
    image = _first(container, lambda node: node.tag == "img")
    if image is not None and (url := _image_url(image, base_url)):
        return url
    if url := _style_image_url(container, base_url):
        return url
    styled = _first(container, lambda node: bool(_style_image_url(node, base_url)))
    return _style_image_url(styled, base_url) if styled is not None else None


class MadaraSource:
    name = "madara"
    display_name = "Madara"
    base_url = ""
    language = ""
    manga_substring = "manga"
    load_more = "auto"
    supports_latest = True
    use_new_chapter_endpoint = False
    chapter_url_suffix = "?style=list"
    requests_per_minute = 60
    pages_profile = "default"
    extra_headers: dict[str, str] = {}
    image_headers: dict[str, str] = {}
    strip_external_image_referer = False
    date_format = "MMMM dd, yyyy"
    date_locale = "en"
    details_profile = "default"
    api_version = SOURCE_API_VERSION
    content_warning = "unknown"
    requires_auth = False

    def __init__(self, fetcher: SourceFetcher | None = None) -> None:
        self.fetcher = fetcher
        self._load_more_detected = self.load_more == "always"
        self.capabilities = SourceCapabilities(
            search=True,
            browse=True,
            headers={
                "User-Agent": "Nyanko/0.2.4",
                "Referer": f"{self.base_url}/",
                **self.extra_headers,
            },
            requests_per_minute=self.requests_per_minute,
            content_warning=self.content_warning,
            requires_auth=self.requires_auth,
        )

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        response = await self._request(
            "GET",
            f"{self.base_url}/",
            params={"s": query.strip(), "post_type": "wp-manga"},
        )
        response.raise_for_status()
        return self._series(response.text, ("c-tabs-item__content", "manga__item"))[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind == "latest" and not self.supports_latest:
            return []
        if kind not in {"popular", "latest"}:
            return []
        if self.load_more == "always" or (self.load_more == "auto" and self._load_more_detected):
            response = await self._request(
                "POST",
                f"{self.base_url}/wp-admin/admin-ajax.php",
                data={
                    "action": "madara_load_more",
                    "page": str(max(page - 1, 0)),
                    "template": "madara-core/content/content-archive",
                    "vars[paged]": "1",
                    "vars[post_type]": "wp-manga",
                    "vars[post_status]": "publish",
                    "vars[meta_key]": "_wp_manga_views" if kind == "popular" else "_latest_update",
                    "vars[orderby]": "meta_value_num",
                    "vars[order]": "desc",
                    "vars[manga_archives_item_layout]": "big_thumbnail",
                },
            )
        else:
            suffix = "" if page == 1 else f"page/{page}/"
            response = await self._request(
                "GET",
                f"{self.base_url}/{self.manga_substring.strip('/')}/{suffix}",
                params={"m_orderby": "views" if kind == "popular" else "latest"},
            )
        response.raise_for_status()
        root = _parse_html(response.text)
        if self.load_more == "auto":
            self._load_more_detected = any(
                node.tag == "nav" and node.has_class("navigation-ajax")
                for node in root.descendants()
            )
        return self._series_from_root(root, ("page-item-detail", "manga__item"))

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
        return SourceSeries(
            source_id=series_id,
            title=title,
            source_name=self.name,
            cover_url=_image_url(image, str(response.url)) if image else None,
            description=description or None,
            author=", ".join(authors) or None,
            artist=", ".join(artists) or None,
            status=self._madara_status(status_text),
            content_tags=tuple(genres),
            metadata=series.metadata if isinstance(series, SourceSeries) else {},
            web_url=str(response.url),
        )

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

    @staticmethod
    def _madara_status(value: str) -> str | None:
        normalized = " ".join(re.findall(r"\w+", value.casefold()))
        if normalized in {"completed", "completo", "completado", "finalizado", "concluido"}:
            return "completed"
        if normalized in {
            "ongoing", "en curso", "curso", "en marcha", "publicandose", "en emision",
            "emision", "emisión", "en emisión", "ativo", "updating",
        }:
            return "ongoing"
        if normalized in {"on hold", "pausado", "en espera"}:
            return "hiatus"
        if normalized in {"canceled", "cancelado"}:
            return "cancelled"
        return None

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        series_url = urljoin(f"{self.base_url}/", series_id)
        response = await self._request("GET", series_url)
        response.raise_for_status()
        root = _parse_html(response.text)
        items = self._chapter_nodes(root)
        if not items:
            items = self._fallback_chapter_nodes(root)
        holder = _first(root, lambda node: node.attrs.get("id", "").startswith("manga-chapters-holder"))
        if not items and holder is not None:
            if self.use_new_chapter_endpoint:
                response = await self._request(
                    "POST", f"{series_url.rstrip('/')}/ajax/chapters",
                    headers={"X-Requested-With": "XMLHttpRequest"},
                )
            else:
                response = await self._request(
                    "POST",
                    f"{self.base_url}/wp-admin/admin-ajax.php",
                    data={"action": "manga_get_chapters", "manga": holder.attrs.get("data-id", "")},
                    headers={"X-Requested-With": "XMLHttpRequest"},
                )
                if getattr(response, "status_code", 200) == 400:
                    response = await self._request(
                        "POST", f"{series_url.rstrip('/')}/ajax/chapters",
                        headers={"X-Requested-With": "XMLHttpRequest"},
                    )
            response.raise_for_status()
            items = self._chapter_nodes(_parse_html(response.text))
            if not items:
                items = self._fallback_chapter_nodes(_parse_html(response.text))

        result: list[SourceChapter] = []
        seen_chapters: set[str] = set()
        for item in items:
            anchor = _first(item, lambda node: node.tag == "a" and bool(node.attrs.get("href")))
            if anchor is None:
                continue
            title = anchor.text().strip()
            relative_image = _first(item, lambda node: node.tag == "img" and not node.has_class("thumb"))
            relative_link = _first(
                item,
                lambda node: node.tag == "a" and node.parent is not None
                and node.parent.tag == "span" and bool(node.attrs.get("title")),
            )
            date = _first(item, lambda node: node.tag == "span" and node.has_class("chapter-release-date"))
            date_text = (
                relative_image.attrs.get("alt", "") if relative_image is not None
                else relative_link.attrs.get("title", "") if relative_link is not None
                else date.text() if date else ""
            )
            chapter_url = urljoin(series_url, anchor.attrs["href"]).split("?style=paged", 1)[0]
            if self.chapter_url_suffix and not chapter_url.endswith(self.chapter_url_suffix):
                chapter_url += self.chapter_url_suffix
            # El fallback recorre li, div y tr por separado: en un markup anidado
            # el mismo ancla cae dentro de varios contenedores y entra una vez por cada uno.
            if chapter_url in seen_chapters:
                continue
            seen_chapters.add(chapter_url)
            match = re.search(r"(?:chapter|cap(?:í|i)tulo|ch)[^\d]*(\d+(?:\.\d+)?)", title, re.I)
            result.append(
                SourceChapter(
                    source_id=chapter_url,
                    title=title or "Capítulo",
                    series_id=series_id,
                    source_name=self.name,
                    number=float(match.group(1)) if match else None,
                    language=self.language,
                    uploaded_at=self._madara_date(date_text),
                )
            )
        return result

    def _madara_date(self, value: str) -> str | None:
        from calendar import monthrange
        from datetime import datetime, timedelta

        text = value.strip().casefold()
        now = datetime.now().replace(microsecond=0)
        if text.startswith(("today", "hoy")):
            return now.replace(hour=0, minute=0, second=0).isoformat()
        if text.startswith(("yesterday", "ayer")):
            return (now - timedelta(days=1)).replace(hour=0, minute=0, second=0).isoformat()
        relative = re.search(r"(\d+)", text)
        if relative and (text.startswith("hace") or text.endswith(("ago", "atrás"))):
            amount = int(relative.group())
            if any(unit in text for unit in ("día", "dia", "day")):
                return (now - timedelta(days=amount)).isoformat()
            if any(unit in text for unit in ("hora", "hour")):
                return (now - timedelta(hours=amount)).isoformat()
            if any(unit in text for unit in ("minuto", "minute", " min")):
                return (now - timedelta(minutes=amount)).isoformat()
            if any(unit in text for unit in ("segundo", "second")):
                return (now - timedelta(seconds=amount)).isoformat()
            if any(unit in text for unit in ("semana", "week")):
                return (now - timedelta(days=amount * 7)).isoformat()
            if any(unit in text for unit in ("mes", "month")):
                total = now.year * 12 + now.month - 1 - amount
                year, month = divmod(total, 12)
                return now.replace(
                    year=year, month=month + 1,
                    day=min(now.day, monthrange(year, month + 1)[1]),
                ).isoformat()
            if any(unit in text for unit in ("año", "year")):
                year = now.year - amount
                return now.replace(year=year, day=min(now.day, monthrange(year, now.month)[1])).isoformat()
        numeric_format = {
            "MM/dd/yyyy": "%m/%d/%Y", "dd/MM/yyyy": "%d/%m/%Y", "yyyy-MM-dd": "%Y-%m-%d",
        }.get(self.date_format)
        if numeric_format:
            try:
                return datetime.strptime(value.strip(), numeric_format).isoformat()
            except ValueError:
                return None
        if self.date_format not in {"d MMMM, yyyy", "dd MMM yyyy", "dd MMM, yyyy", "dd MMMM, yyyy", "MMM dd, yyyy", "MMMM dd, yyyy"}:
            return None
        months = {
            "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
            "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
            "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
            "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
            "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
            "ene": 1, "abr": 4, "ago": 8, "dic": 12,
        }
        day_first = self.date_format.startswith(("d ", "dd "))
        absolute = (
            re.fullmatch(r"(\d{1,2})\s+([^\s,]+),?\s+(\d{4})", text)
            if day_first
            else re.fullmatch(r"([^\s]+)\s+(\d{1,2}),\s*(\d{4})", text)
        )
        month = absolute.group(2).rstrip(".") if absolute and day_first else absolute.group(1).rstrip(".") if absolute else ""
        if absolute and month in months:
            day = absolute.group(1) if day_first else absolute.group(2)
            return datetime(int(absolute.group(3)), months[month], int(day)).isoformat()
        return None

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        response = await self._request("GET", urljoin(f"{self.base_url}/", chapter_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        blocked = _first(
            root,
            lambda node: node.has_class("login-required")
            or (
                node.tag in {"form", "input"}
                and (
                    self.pages_profile == "captcha_guard"
                    or node.attrs.get("value", "").lower() in {"doğrula", "verify"}
                )
            ),
        )
        if blocked is not None and self.pages_profile in {"login_guard", "captcha_guard"}:
            raise ValueError("El capítulo requiere iniciar sesión o resolver el captcha en WebView")

        profile_urls = self._profile_page_urls(response.text, str(response.url))
        if self.pages_profile == "campaign":
            redirect = _first(
                root,
                lambda node: node.tag == "a"
                and bool(parse_qs(urlparse(node.attrs.get("href", "")).query).get("a")),
            )
            if redirect is not None:
                target = unquote(parse_qs(urlparse(redirect.attrs["href"]).query)["a"][0])
                campaign = await self._request(
                    "GET",
                    f"{self.base_url}/campanha.php",
                    params={"auth": target},
                )
                campaign.raise_for_status()
                campaign_root = _parse_html(campaign.text)
                profile_urls = [
                    _image_url(image, str(campaign.url))
                    for image in campaign_root.descendants("img")
                    if self._has_class_ancestor(image, "manga-content")
                ]
        containers = [
            node
            for node in root.descendants()
            if (node.tag == "div" and node.has_class("page-break"))
            or (node.tag == "li" and node.has_class("blocks-gallery-item"))
        ]
        images = [
            image
            for container in containers
            if (image := _first(container, lambda node: node.tag == "img")) is not None
        ]
        reading = _first(root, lambda node: node.has_class("reading-content"))
        if reading is not None and self.pages_profile != "page_break_only":
            images.extend(reading.descendants("img"))
        if not images:
            images = [
                image
                for image in root.descendants("img")
                if self._has_reader_ancestor(image)
            ]

        urls = list(
            dict.fromkeys(
                url for image in images if (url := _image_url(image, str(response.url)))
            )
        )
        if profile_urls:
            urls = profile_urls
        if not urls:
            script_text = response.text
            encoded = re.search(
                r"""<script[^>]+src=["']data:text/javascript;base64,([^"']+)""",
                script_text,
                re.I,
            )
            if encoded:
                try:
                    script_text += base64.b64decode(encoded.group(1)).decode()
                except (ValueError, UnicodeDecodeError):
                    pass
            match = re.search(r"""["']?images["']?\s*:\s*(\[.*?])""", script_text, re.S)
            if match:
                try:
                    values = json.loads(match.group(1))
                except (json.JSONDecodeError, TypeError):
                    try:
                        values = ast.literal_eval(match.group(1))
                    except (ValueError, SyntaxError):
                        values = []
                urls = [urljoin(str(response.url), str(value)) for value in values]
        if self.pages_profile == "https":
            urls = [url.replace("http://", "https://", 1) for url in urls]
        elif self.pages_profile == "skip_placeholder" and urls:
            if urls[0].split("?", 1)[0].endswith("/1-000001.jpg"):
                urls = urls[1:]
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

    async def page_bytes(self, page: SourcePage | str) -> SourcePageContent:
        url = page.source_id if isinstance(page, SourcePage) else page
        if not url:
            raise SourceNotFoundError("Página Madara sin URL")
        parsed = urlparse(url)
        headers = dict(self.image_headers)
        if isinstance(page, SourcePage) and not (
            self.strip_external_image_referer
            and parsed.hostname != urlparse(self.base_url).hostname
        ):
            headers.setdefault("Referer", page.chapter_id)
        response = await self._request(
            "GET",
            urlunparse(parsed._replace(fragment="")),
            headers=headers,
        )
        response.raise_for_status()
        content = response.content
        if parsed.fragment and self.pages_profile == "scrambled":
            data = json.loads(unquote(parsed.fragment))
            source = Image.open(io.BytesIO(content)).convert("RGBA")
            output = Image.new("RGBA", source.size)
            width, height = int(data["blockWidth"]), int(data["blockHeight"])
            for dest_x, dest_y, src_x, src_y, *_ in data["matrix"]:
                block = source.crop((int(src_x), int(src_y), int(src_x) + width, int(src_y) + height))
                output.paste(block, (int(dest_x), int(dest_y)))
            buffer = io.BytesIO()
            output.convert("RGB").save(buffer, "JPEG", quality=90)
            content = buffer.getvalue()
        return SourcePageContent(
            media_type="image/jpeg" if parsed.fragment else response.headers.get("Content-Type", "image/jpeg"),
            chunks=iter([content]),
        )

    def _profile_page_urls(self, html: str, base_url: str) -> list[str]:
        if self.pages_profile == "arraydata":
            match = re.search(r"""<p[^>]+id=["']arraydata["'][^>]*>(.*?)</p>""", html, re.I | re.S)
            return [urljoin(base_url, value.strip()) for value in match.group(1).split(",") if value.strip()] if match else []
        if self.pages_profile == "hentairead":
            base = re.search(r"""["']baseUrl["']\s*:\s*["']([^"']+)""", html)
            encoded = re.search(r"""\b(eyJ[A-Za-z0-9+/=_-]+)\b""", html)
            if not encoded:
                return []
            try:
                payload = json.loads(base64.b64decode(encoded.group(1) + "=="))
            except (ValueError, json.JSONDecodeError):
                return []
            images = payload.get("data", {}).get("chapter", {}).get("images", [])
            return [urljoin(f"{base.group(1)}/" if base else base_url, str(item.get("src", ""))) for item in images if item.get("src")]
        patterns = {
            "cerise": r"""content\s*:\s*(\[[\s\S]*?])""",
            "preloaded": r"""chapter_preloaded_images\s*=\s*(\[[\s\S]*?])""",
        }
        if pattern := patterns.get(self.pages_profile):
            match = re.search(pattern, html)
            if not match:
                return []
            try:
                values = json.loads(match.group(1))
            except json.JSONDecodeError:
                return []
            return [urljoin(base_url, str(value)) for value in values]
        if self.pages_profile == "base64_pages":
            match = re.search(r"""var\s+pages\s*=\s*\[([\s\S]*?)]""", html)
            if not match:
                return []
            result = []
            for encoded in re.findall(r"""["']([^"']+)["']""", match.group(1)):
                try:
                    result.append(base64.b64decode(encoded).decode())
                except (ValueError, UnicodeDecodeError):
                    continue
            return result
        if self.pages_profile == "scrambled":
            result: list[str] = []
            for script in re.findall(r"<script[^>]*>([\s\S]*?)</script>", html, re.I):
                if "p,a,c,k,e,d" not in script:
                    continue
                unpacked = self._unpack_packer(script)
                width = re.search(r"""width:\s*["']?\s*\+?\s*(\d+)""", unpacked)
                height = re.search(r"""height:\s*["']?\s*\+?\s*(\d+)""", unpacked)
                matrix = re.search(r"(\[\s*\[.*?]])\s*;", unpacked, re.S)
                image_url = re.search(r"""url\((['"]?)(.*?)\1\);""", unpacked)
                if all((width, height, matrix, image_url)):
                    data = {
                        "blockWidth": int(width.group(1)),
                        "blockHeight": int(height.group(1)),
                        "matrix": json.loads(matrix.group(1)),
                    }
                    result.append(f"{urljoin(base_url, image_url.group(2))}#{json.dumps(data, separators=(',', ':'))}")
            return result
        return []

    @staticmethod
    def _unpack_packer(source: str) -> str:
        match = re.search(
            r"""\}\s*\(\s*(['"])(.*?)\1\s*,\s*(\d+)\s*,\s*\d+\s*,\s*(['"])(.*?)\4\.split\(\s*['"]\|['"]\s*\)""",
            source,
            re.S,
        )
        if match is None:
            return ""
        payload = bytes(match.group(2), "utf-8").decode("unicode_escape")
        radix = int(match.group(3))
        words = match.group(5).split("|")
        alphabet = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"

        def decode(value: str) -> int:
            result = 0
            for char in value:
                result = result * radix + alphabet.index(char)
            return result

        return re.sub(
            r"\b[0-9a-zA-Z]+\b",
            lambda found: words[index] if (index := decode(found.group())) < len(words) and words[index] else found.group(),
            payload,
        )

    def _series(self, html: str, classes: tuple[str, ...]) -> list[SourceSeries]:
        return self._series_from_root(_parse_html(html), classes)

    def _series_from_root(self, root: _Node, classes: tuple[str, ...]) -> list[SourceSeries]:
        result: list[SourceSeries] = []
        seen: set[str] = set()
        for item in root.descendants():
            if not any(item.has_class(name) for name in classes):
                continue
            title_box = _first(item, lambda node: node.has_class("post-title"))
            anchor = _first(title_box or item, lambda node: node.tag == "a" and bool(node.attrs.get("href")))
            if anchor is None:
                continue
            source_id = urljoin(f"{self.base_url}/", anchor.attrs["href"])
            title = anchor.text().strip() or anchor.attrs.get("title", "").strip()
            if source_id in seen or not title:
                continue
            seen.add(source_id)
            result.append(
                SourceSeries(
                    source_id=source_id,
                    title=title,
                    source_name=self.name,
                    cover_url=_cover_url(item, self.base_url),
                    web_url=source_id,
                )
            )
        if result:
            return result
        route = self.manga_substring.strip("/")
        for anchor in root.descendants("a"):
            href = anchor.attrs.get("href", "")
            parts = [part for part in urljoin(f"{self.base_url}/", href).split("?", 1)[0].split("/") if part]
            if not href or route not in parts:
                continue
            route_index = parts.index(route)
            if len(parts) > route_index + 2:
                continue
            source_id = urljoin(f"{self.base_url}/", href)
            title = anchor.attrs.get("title", "").strip() or anchor.text().strip()
            if title and source_id not in seen:
                seen.add(source_id)
                result.append(
                    SourceSeries(
                        source_id=source_id,
                        title=title,
                        source_name=self.name,
                        cover_url=_cover_url(anchor, self.base_url),
                        web_url=source_id,
                    )
                )
        return result

    @staticmethod
    def _chapter_nodes(root: _Node) -> list[_Node]:
        return [
            node
            for node in root.descendants("li")
            if node.has_class("wp-manga-chapter")
        ]

    @staticmethod
    def _fallback_chapter_nodes(root: _Node) -> list[_Node]:
        result: list[_Node] = []
        for node in root.descendants():
            if node.tag not in {"li", "div", "tr"}:
                continue
            anchor = _first(node, lambda item: item.tag == "a" and bool(item.attrs.get("href")))
            if anchor is None:
                continue
            value = f"{node.attrs.get('class', '')} {anchor.attrs['href']} {anchor.text()}".lower()
            if any(marker in value for marker in ("chapter", "chap", "capitulo", "capítulo", "episode")):
                result.append(node)
        return result

    @staticmethod
    def _has_reader_ancestor(node: _Node) -> bool:
        parent = node.parent
        while parent is not None:
            marker = f"{parent.attrs.get('id', '')} {parent.attrs.get('class', '')}".lower()
            if "related-reading" in marker:
                return False
            if any(value in marker for value in ("reading-content", "read-content", "reader", "ch-images")):
                return True
            parent = parent.parent
        return False

    @staticmethod
    def _has_class_ancestor(node: _Node, class_name: str) -> bool:
        parent = node.parent
        while parent is not None:
            if parent.has_class(class_name):
                return True
            parent = parent.parent
        return False

    @staticmethod
    def _has_id_ancestor(node: _Node, identifier: str) -> bool:
        parent = node.parent
        while parent is not None:
            if parent.attrs.get("id") == identifier:
                return True
            parent = parent.parent
        return False

    async def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        if self.fetcher is None:
            raise SourceNotFoundError(f"{self.display_name} no tiene fetcher inyectado")
        return await self.fetcher.request(method, url, **kwargs)


class SamuraiScanSource(MadaraSource):
    async def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        kwargs.setdefault("follow_redirects", True)
        return await super()._request(method, url, **kwargs)


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


class MangasNoSekaiSource(MadaraSource):
    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"}:
            return []
        suffix = "" if page == 1 else f"page/{page}/"
        response = await self._request(
            "GET",
            f"{self.base_url}/biblioteca/{suffix}",
            params={"m_orderby": "views" if kind == "popular" else "latest"},
        )
        response.raise_for_status()
        result = []
        for item in _parse_html(response.text).descendants("div"):
            parent = item.parent
            if not (
                parent is not None and parent.has_class("row")
                and parent.parent is not None and parent.parent.has_class("page-listing-item")
            ):
                continue
            anchor = _first(item, lambda node: node.tag == "a" and bool(node.attrs.get("href")))
            title = _first(item, lambda node: node.tag == "figcaption")
            if anchor is None or title is None:
                continue
            image = _first(item, lambda node: node.tag == "img")
            url = urljoin(str(response.url), anchor.attrs["href"])
            result.append(SourceSeries(
                source_id=url, title=title.text().strip(), source_name=self.name,
                cover_url=_image_url(image, str(response.url)) if image else None, web_url=url,
            ))
        return result

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        synopsis = _first(root, lambda node: node.tag == "section" and node.attrs.get("id") == "section-sinopsis")

        def row(label: str) -> _Node | None:
            if synopsis is None:
                return None
            return _first(
                synopsis,
                lambda node: node.tag == "div" and node.has_class("d-flex")
                and any(child.tag == "div" and label in child.text() for child in node.descendants("div")),
            )

        def value(label: str) -> _Node | None:
            item = row(label)
            return _first(item, lambda node: node.tag == "p") if item else None

        title = _first(root, lambda node: node.tag == "p" and node.has_class("titleMangaSingle"))
        image = _first(
            root,
            lambda node: node.tag == "img" and node.has_class("img-responsive")
            and self._has_class_ancestor(node, "thumble-container"),
        )
        description = next(
            (child for child in synopsis.children if isinstance(child, _Node) and child.tag == "p"),
            None,
        ) if synopsis else None
        author = value("Autor")
        status = value("Estado")
        genre = value("Generos")
        alt_name = value("Otros nombres")
        description_text = description.text().strip() if description else ""
        if alt_name and alt_name.text().strip() and "updating" not in alt_name.text().casefold():
            description_text = f"{description_text}\n\nOtros nombres: {alt_name.text().strip()}".strip()
        genres = tuple(
            anchor.text().strip().capitalize()
            for anchor in genre.descendants("a") if anchor.text().strip()
        ) if genre else ()
        return SourceSeries(
            source_id=series_id,
            title=title.text().strip() if title else series.title if isinstance(series, SourceSeries) else series_id.rstrip("/").rsplit("/", 1)[-1],
            source_name=self.name,
            cover_url=_image_url(image, str(response.url)) if image else None,
            description=description_text or None,
            author=", ".join(anchor.text().strip() for anchor in author.descendants("a") if anchor.text().strip()) if author else None,
            status=self._madara_status(status.text() if status else ""),
            content_tags=genres,
            metadata=series.metadata if isinstance(series, SourceSeries) else {},
            web_url=str(response.url),
        )

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        series_url = urljoin(f"{self.base_url}/", series_id)
        response = await self._request("GET", series_url)
        response.raise_for_status()
        root = _parse_html(response.text)
        script = _first(root, lambda node: node.tag == "script" and node.attrs.get("id") == "wp-manga-js")
        if script is None or not script.attrs.get("src"):
            raise ValueError("No se pudo obtener el script de capítulos")
        script_response = await self._request("GET", urljoin(str(response.url), script.attrs["src"]))
        script_response.raise_for_status()
        endpoint, fields = self._ajax_config(script_response.text)
        extra = _first(root, lambda node: node.tag == "script" and node.attrs.get("id") == "wp-manga-js-extra")
        fallback = _first(root, lambda node: node.tag == "script" and node.attrs.get("id") == "manga_disqus_embed-js-extra")
        manga_id = re.search(r'''["']manga_id["']\s*:\s*["']([^"']+)''', extra.text() if extra else "")
        manga_id = manga_id or re.search(r'''["']postId["']\s*:\s*["']([^"']+)''', fallback.text() if fallback else "")
        if manga_id is None:
            raise ValueError("No se pudo obtener el id del manga")

        result = []
        page = 1
        while True:
            chapter_response = await self._request(
                "POST", urljoin(f"{self.base_url}/", endpoint),
                data={"mangaid": manga_id.group(1), "page": str(page), **fields},
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
            chapter_response.raise_for_status()
            payload = chapter_response.json() if hasattr(chapter_response, "json") else json.loads(chapter_response.text)
            for item in payload.get("chapters_to_display", []):
                title_text = str(item.get("name", "")).strip()
                number = re.search(r"\d+(?:\.\d+)?", title_text)
                date_text = _parse_html(str(item.get("date", ""))).text()
                result.append(SourceChapter(
                    source_id=urljoin(series_url, str(item.get("link", ""))).rstrip("/"),
                    title=title_text or "Capítulo", series_id=series_id, source_name=self.name,
                    number=float(number.group()) if number else None, language=self.language,
                    uploaded_at=self._madara_date(date_text),
                ))
            if int(payload.get("current_page", page)) >= int(payload.get("total_pages", page)):
                return result
            page += 1

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else str(chapter)
        return await super().pages(f"{chapter_id.rstrip('/')}/")

    @staticmethod
    def _ajax_config(script: str) -> tuple[str, dict[str, str]]:
        array = re.search(r"\b(?:var|let|const)\s+(\w+)\s*=\s*(\[.*?])\s*;", script, re.S)
        variants = [script]
        if array:
            try:
                values = ast.literal_eval(array.group(2))
            except (SyntaxError, ValueError):
                values = []
            for function in re.finditer(r"(?:\b(?:var|let|const)\s+)?(\w+)\s*=\s*function\s*\((\w+)[^)]*\)\s*\{(.*?)\}", script, re.S):
                decoder, argument, body = function.groups()
                offset = re.search(rf"\b{re.escape(argument)}\s*=\s*{re.escape(argument)}\s*-\s*(0x[\da-f]+|\d+)", body, re.I)
                if not values or not offset or not re.search(rf"\b{re.escape(array.group(1))}\s*\[", body):
                    continue
                base = int(offset.group(1), 0)
                calls = re.compile(rf"\b{re.escape(decoder)}\(\s*(['\"])(0x[\da-f]+|\d+)\1[^)]*\)", re.I)
                for shift in range(len(values)):
                    rotated = values[shift:] + values[:shift]
                    variants.append(calls.sub(
                        lambda match: json.dumps(rotated[int(match.group(2), 0) - base])
                        if 0 <= int(match.group(2), 0) - base < len(rotated) else match.group(),
                        script,
                    ))
        pattern = re.compile(
            r'''function\s+.*?\.ajax\b.*?['"]?url['"]?\s*:\s*(['"])(.*?)\1(?:.*?['"]?data['"]?\s*:\s*\{(.*?)\})?''',
            re.S,
        )
        for candidate in variants:
            found = pattern.search(candidate)
            if found and (found.group(2).startswith("/") or found.group(2).startswith("http")):
                fields = {
                    item.group(1): item.group(3)
                    for item in re.finditer(r'''['"]?(\w+)['"]?\s*:\s*(['"])(.*?)\2''', found.group(3) or "")
                }
                return found.group(2), fields
        raise ValueError("No se pudo obtener el endpoint de capítulos")


class MangaCrabSource(MadaraSource):
    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind == "popular":
            if page > 1:
                return []
            response = await self._request("GET", self.base_url)
            profile = "popular"
        elif kind == "latest":
            response = await self._request("GET", f"{self.base_url}/page/{page}/")
            profile = "latest"
        else:
            return []
        response.raise_for_status()
        return self._crab_series(_parse_html(response.text), profile, str(response.url))

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        response = await self._request("GET", f"{self.base_url}/page/1/", params={"s": query.strip()})
        response.raise_for_status()
        return self._crab_series(_parse_html(response.text), "search", str(response.url))[:limit]

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        series_url = urljoin(f"{self.base_url}/", series_id)
        response = await self._request("GET", series_url)
        response.raise_for_status()
        root = _parse_html(response.text)
        holder = _first(root, lambda node: node.attrs.get("id") == "mv-chapter-list")
        manga_id = holder.attrs.get("data-manga-id", "") if holder else ""
        manga_id_match = re.search(r'''["']manga_id["']\s*:\s*["']?(\d+)''', response.text)
        manga_id = manga_id or (manga_id_match.group(1) if manga_id_match else "")
        nonce_match = re.search(
            r'''var\s+mvTheme\s*=\s*\{[^}]*["']nonce["']\s*:\s*["']([^"']+)''',
            response.text,
        ) or re.search(r'''["']nonce["']\s*:\s*["']([^"']+)''', response.text)
        chapters: list[SourceChapter] = []
        if manga_id and nonce_match:
            page = 1
            seen: set[str] = set()
            while True:
                try:
                    chapter_response = await self._request(
                        "POST",
                        f"{self.base_url}/wp-admin/admin-ajax.php",
                        data={
                            "action": "mv_get_chapters", "nonce": nonce_match.group(1),
                            "manga_id": manga_id, "page": str(page), "search": "",
                        },
                        headers={"X-Requested-With": "XMLHttpRequest"},
                    )
                    chapter_response.raise_for_status()
                    payload = chapter_response.json() if hasattr(chapter_response, "json") else json.loads(chapter_response.text)
                except Exception:
                    break
                success = payload.get("success")
                if success not in {True, "true"}:
                    break
                data = payload.get("data") or {}
                anchors = self._crab_chapter_anchors(_parse_html(data.get("list") or ""))
                fresh = [chapter for anchor in anchors if (chapter := self._crab_chapter(anchor, series_id, series_url)).source_id not in seen]
                if not fresh:
                    break
                chapters.extend(fresh)
                seen.update(chapter.source_id for chapter in fresh)
                page += 1
        if chapters:
            return chapters
        return [self._crab_chapter(anchor, series_id, series_url) for anchor in self._crab_chapter_anchors(root)]

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else str(chapter)
        response = await self._request("GET", urljoin(f"{self.base_url}/", chapter_id))
        response.raise_for_status()
        header = re.search(r'''["']imgHeader["']\s*:\s*["']([^"']+)["']''', response.text)
        urls = []
        for image in _parse_html(response.text).descendants("img"):
            page_break = self._ancestor_with_class(image, "page-break")
            if not (
                image.has_class("mv-secure-img")
                or self._has_class_ancestor(image, "reader-body")
                or self._has_id_ancestor(image, "mv-reader-body")
                or page_break is not None and "display:none" not in page_break.attrs.get("style", "").replace(" ", "") and not image.attrs.get("src")
            ):
                continue
            if url := _image_url(image, str(response.url)):
                urls.append(f"{url}#nodeHeader={header.group(1)}" if header else url)
        return [SourcePage(
            source_id=url,
            chapter_id=chapter_id,
            index=index,
            filename=urlparse(url).path.rsplit("/", 1)[-1] or f"{index}.jpg",
            source_name=self.name,
        ) for index, url in enumerate(dict.fromkeys(urls), 1)]

    async def page_bytes(self, page: SourcePage | str) -> SourcePageContent:
        url = page.source_id if isinstance(page, SourcePage) else str(page)
        parsed = urlparse(url)
        headers = {"Referer": page.chapter_id} if isinstance(page, SourcePage) else {}
        if parsed.fragment.startswith("nodeHeader="):
            headers["Node"] = unquote(parsed.fragment.removeprefix("nodeHeader="))
        response = await self._request("GET", urlunparse(parsed._replace(fragment="")), headers=headers)
        response.raise_for_status()
        return SourcePageContent(
            media_type=response.headers.get("Content-Type", "image/jpeg"),
            chunks=iter([response.content]),
        )

    def _crab_series(self, root: _Node, profile: str, base_url: str) -> list[SourceSeries]:
        wanted = {
            "popular": ("mv-rank-item",),
            "latest": ("manga-row",),
            "search": ("catalog-card", "mv-recent-card", "manga-row", "manga__item"),
        }[profile]
        result: list[SourceSeries] = []
        for item in root.descendants():
            if not any(item.has_class(value) for value in wanted):
                continue
            preferred = "manga-row-cover" if profile == "latest" else "mv-recent-link" if profile == "search" else ""
            anchor = _first(item, lambda node: node.tag == "a" and bool(node.attrs.get("href")) and (not preferred or node.has_class(preferred)))
            if anchor is None:
                anchor = _first(item, lambda node: node.tag == "a" and bool(node.attrs.get("href")))
            title = _first(item, lambda node: node.has_class("mv-rank-title") or node.has_class("mv-recent-name") or node.tag in {"h2", "h5"})
            if anchor is None:
                continue
            source_id = urljoin(base_url, anchor.attrs["href"])
            image = _first(item, lambda node: node.tag == "img")
            result.append(SourceSeries(
                source_id=source_id,
                title=(title.text() if title else anchor.text()).strip(),
                source_name=self.name,
                cover_url=_image_url(image, base_url) if image else None,
                web_url=source_id,
            ))
        return list({item.source_id: item for item in result}.values())

    def _crab_chapter_anchors(self, root: _Node) -> list[_Node]:
        return [
            anchor for anchor in root.descendants("a") if anchor.attrs.get("href")
            and (
                self._has_class_ancestor(anchor, "chapter-item")
                or self._has_id_ancestor(anchor, "mv-chapter-list")
            )
        ]

    def _crab_chapter(self, anchor: _Node, series_id: str, series_url: str) -> SourceChapter:
        title = anchor.text().strip()
        number = re.search(r"\d+(?:\.\d+)?", title)
        return SourceChapter(
            source_id=urljoin(series_url, anchor.attrs["href"]),
            title=title or "Capítulo",
            series_id=series_id,
            source_name=self.name,
            number=float(number.group()) if number else None,
            language=self.language,
        )

    @staticmethod
    def _ancestor_with_class(node: _Node, class_name: str) -> _Node | None:
        parent = node.parent
        while parent is not None:
            if parent.has_class(class_name):
                return parent
            parent = parent.parent
        return None


class InfraFandubSource(MadaraSource):
    def _series_from_root(self, root: _Node, classes: tuple[str, ...]) -> list[SourceSeries]:
        result: list[SourceSeries] = []
        for item in root.descendants("div"):
            if not item.has_class("manga-item"):
                continue
            title = _first(item, lambda node: node.tag == "div" and node.has_class("title"))
            anchor = _first(title or item, lambda node: node.tag == "a" and bool(node.attrs.get("href")))
            if anchor is None:
                continue
            url = urljoin(f"{self.base_url}/", anchor.attrs["href"])
            image = _first(item, lambda node: node.tag == "img")
            result.append(SourceSeries(
                source_id=url,
                title=anchor.text().strip() or anchor.attrs.get("title", "").strip(),
                source_name=self.name,
                cover_url=_image_url(image, self.base_url) if image else None,
                web_url=url,
            ))
        return result

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        title = _first(root, lambda node: node.tag == "h1" and node.has_class("series-title"))
        image = _first(
            root,
            lambda node: node.tag == "img" and node.has_class("series-cover")
            and self._has_class_ancestor(node, "sidebar"),
        )
        description = _first(root, lambda node: node.tag == "div" and node.has_class("summary-text"))

        def detail(label: str) -> str:
            for item in root.descendants("div"):
                if item.has_class("detail-item") and label in item.text().casefold():
                    value = _first(item, lambda node: node.tag == "span" and node.has_class("detail-value"))
                    return value.text().strip() if value else ""
            return ""

        genres = tuple(
            node.text().strip() for node in root.descendants("a")
            if node.has_class("genre-tag") and self._has_class_ancestor(node, "genres") and node.text().strip()
        )
        return SourceSeries(
            source_id=series_id,
            title=title.text().strip() if title else series.title if isinstance(series, SourceSeries) else series_id.rstrip("/").rsplit("/", 1)[-1],
            source_name=self.name,
            cover_url=_image_url(image, str(response.url)) if image else None,
            description=description.text().strip() if description else None,
            author=detail("autor") or None,
            artist=detail("artista") or None,
            status=self._madara_status(detail("estado")),
            content_tags=genres,
            web_url=str(response.url),
        )

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        series_url = urljoin(f"{self.base_url}/", series_id).rstrip("/")
        response = await self._request("POST", f"{series_url}/ajax/chapters/")
        response.raise_for_status()
        result: list[SourceChapter] = []
        for anchor in _parse_html(response.text).descendants("a"):
            if not anchor.has_class("chapter-item") or not anchor.attrs.get("href"):
                continue
            title = _first(anchor, lambda node: node.tag == "span" and node.has_class("chapter-number"))
            date = _first(anchor, lambda node: node.tag == "span" and node.has_class("chapter-date"))
            name = title.text().strip() if title else anchor.text().strip()
            number = re.search(r"\d+(?:\.\d+)?", name)
            result.append(SourceChapter(
                source_id=urljoin(str(response.url), anchor.attrs["href"]),
                title=name,
                series_id=series_id,
                source_name=self.name,
                number=float(number.group()) if number else None,
                language=self.language,
                uploaded_at=self._madara_date(date.text() if date else ""),
            ))
        return result


class DragonTranslationOrgSource(MadaraSource):
    def __init__(self, fetcher=None) -> None:
        super().__init__(fetcher)
        self._genres: list[tuple[str, str]] | None = None
        self._genre_attempts = 0

    async def get_filters(self) -> list[SourceFilter]:
        if self._genres is None and self._genre_attempts < 3:
            self._genre_attempts += 1
            try:
                response = await self._request(
                    "GET", f"{self.base_url}/", params={"s": "genre", "post_type": "wp-manga"},
                )
                response.raise_for_status()
                root = _parse_html(response.text)
                group = _first(root, lambda node: node.tag == "div" and node.has_class("checkbox-group"))
                self._genres = [] if group is None else [
                    (control.attrs.get("value", ""), label.text().strip())
                    for box in group.descendants("div") if box.has_class("checkbox")
                    if (label := _first(box, lambda node: node.tag == "label")) is not None
                    and (control := _first(box, lambda node: node.tag == "input" and node.attrs.get("type") == "checkbox")) is not None
                ]
            except Exception:
                pass
        filters = [
            SourceFilter("author", "Autor", "text", default=""),
            SourceFilter("artist", "Artista", "text", default=""),
            SourceFilter("year", "Ano de publicacion", "text", default=""),
            SourceFilter("status", "Estado", "multi_select", [
                ("end", "Completado"), ("on-going", "En curso"),
                ("canceled", "Cancelado"), ("on-hold", "En espera"),
            ], []),
            SourceFilter("order", "Ordenar por", "select", [
                ("", "Relevancia"), ("latest", "Mas recientes"), ("alphabet", "A-Z"),
                ("rating", "Valoracion"), ("trending", "Tendencia"),
                ("views", "Mas vistos"), ("new-manga", "Nuevos"),
            ], ""),
            SourceFilter("adult", "Contenido adulto", "select", [
                ("", "Todo"), ("0", "Excluir"), ("1", "Solo adulto"),
            ], ""),
        ]
        if self._genres:
            filters.extend([
                SourceFilter("genre_condition", "Condicion de generos", "select", [("", "O"), ("1", "Y")], ""),
                SourceFilter("genres", "Generos", "multi_select", self._genres, []),
            ])
        return filters

    async def browse(self, kind: str, page: int = 1):
        if kind not in {"popular", "latest"}:
            return {"items": [], "has_more": False}
        path = "manga/" if page == 1 else f"manga/page/{page}/"
        response = await self._request(
            "GET", urljoin(f"{self.base_url}/", path),
            params={"m_orderby": "views" if kind == "popular" else "latest"},
        )
        response.raise_for_status()
        return self._dragon_cards(response)

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        query = query.strip()
        if query.startswith("https://"):
            parsed = urlparse(query)
            if parsed.netloc != urlparse(self.base_url).netloc:
                raise ValueError("URL no compatible")
            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) < 2:
                raise ValueError("URL no compatible")
            query = f"slug:{parts[1]}"
        if query.startswith("slug:"):
            response = await self._request("GET", f"{self.base_url}/manga/{query[5:]}/")
            response.raise_for_status()
            return {"items": [self._dragon_details(response)], "has_more": False}
        values = filters or {}
        path = "" if page == 1 else f"page/{page}/"
        params: list[tuple[str, str]] = [("s", query), ("post_type", "wp-manga")]
        for key, parameter in (("author", "author"), ("artist", "artist"), ("year", "release")):
            if str(values.get(key, "")).strip():
                params.append((parameter, str(values[key]).strip()))
        if isinstance(values.get("status"), list):
            params.extend(("status[]", str(status)) for status in values["status"])
        for key, parameter in (("order", "m_orderby"), ("adult", "adult"), ("genre_condition", "op")):
            if key == "adult" or values.get(key):
                params.append((parameter, str(values.get(key, ""))))
        if isinstance(values.get("genres"), list):
            params.extend(("genre[]", str(genre)) for genre in values["genres"])
        response = await self._request("GET", urljoin(f"{self.base_url}/", path), params=params)
        response.raise_for_status()
        return self._dragon_cards(response)

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        script = _first(
            _parse_html(response.text),
            lambda node: node.tag == "script" and node.attrs.get("id") == "mk-chapters-data",
        )
        if script is None:
            raise ValueError("DragonTranslation.org no publico mk-chapters-data")
        payload = json.loads("".join(child for child in script.children if isinstance(child, str)))
        result: list[SourceChapter] = []
        for item in payload.get("items", []):
            title = str(item.get("name", "")).strip()
            url = str(item.get("url", "")).strip()
            if not title or not url:
                continue
            number = re.search(r"\d+(?:\.\d+)?", title)
            result.append(SourceChapter(
                source_id=urljoin(str(response.url), url),
                title=title,
                series_id=series_id,
                source_name=self.name,
                number=float(number.group()) if number else None,
                language=self.language,
                uploaded_at=self._dragon_date(str(item.get("ago", ""))),
            ))
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        response = await self._request("GET", urljoin(f"{self.base_url}/", chapter_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        elements = [
            node for node in root.descendants()
            if (node.tag == "div" and node.has_class("page-break"))
            or (node.tag == "li" and node.has_class("blocks-gallery-item"))
            or (
                node.tag == "img" and (text_left := self._class_ancestor(node, "text-left")) is not None
                and self._has_class_ancestor(node, "reading-content")
                and not any(child.has_class("blocks-gallery-item") for child in text_left.descendants())
            )
        ]
        urls = []
        for element in elements:
            image = element if element.tag == "img" else _first(element, lambda node: node.tag == "img")
            if image is not None and (url := _image_url(image, str(response.url))):
                urls.append(url)
        return [SourcePage(
            source_id=url,
            chapter_id=chapter_id,
            index=index,
            filename=urlparse(url).path.rsplit("/", 1)[-1] or f"{index}.jpg",
            source_name=self.name,
        ) for index, url in enumerate(urls, 1)]

    def _dragon_cards(self, response) -> dict:
        root = _parse_html(response.text)
        items: list[SourceSeries] = []
        for card in root.descendants("a"):
            if (
                not card.has_class("acard") or card.parent is None
                or card.parent.tag != "div" or card.parent.attrs.get("id") != "mkAgrid"
            ):
                continue
            title = _first(card, lambda node: node.tag == "div" and node.has_class("ac-t"))
            if title is None or not card.attrs.get("href"):
                continue
            source_id = urljoin(str(response.url), card.attrs["href"])
            image = _first(card, lambda node: node.tag == "img")
            items.append(SourceSeries(
                source_id=source_id,
                title=self._own_text(title),
                source_name=self.name,
                cover_url=_image_url(image, str(response.url)) if image else None,
                web_url=source_id,
            ))
        has_more = any(
            node.tag == "a" and node.has_class("nextpostslink")
            and node.parent is not None and node.parent.tag == "div" and node.parent.has_class("wp-pagenavi")
            for node in root.descendants("a")
        )
        return {"items": items, "has_more": has_more}

    def _dragon_details(self, response) -> SourceSeries:
        root = _parse_html(response.text)
        hcol = _first(root, lambda node: node.tag == "div" and node.has_class("hcol"))
        poster = _first(root, lambda node: node.tag == "div" and node.has_class("hposter__card"))
        synopsis = _first(root, lambda node: node.tag == "div" and node.attrs.get("id") == "syn")
        if hcol is None:
            raise ValueError("DragonTranslation.org no publico los detalles")
        title = self._direct_child(hcol, lambda node: node.has_class("htitle"))
        tags = self._direct_child(hcol, lambda node: node.has_class("htags"))
        status_node = self._direct_child(tags, lambda node: node.has_class("htag--status")) if tags else None
        genres_box = self._direct_child(hcol, lambda node: node.has_class("hchips--genres"))
        status_text = status_node.text().strip().lower() if status_node else ""
        status = (
            "completed" if status_text in {"completed", "completo", "completado", "finalizado"}
            else "ongoing" if status_text in {"ongoing", "en curso", "emision", "publicandose", "publicandose"}
            else "hiatus" if status_text in {"on hold", "pausado", "en espera"}
            else "cancelled" if status_text in {"canceled", "cancelado"}
            else None
        )
        image = self._direct_child(poster, lambda node: node.tag == "img") if poster else None
        paragraphs = [child.text().strip() for child in synopsis.children if isinstance(child, _Node) and child.tag == "p"] if synopsis else []
        authors = [
            node.text().strip() for node in root.descendants("a")
            if node.parent is not None and node.parent.has_class("author-content") and node.text().strip()
        ]
        authors.extend(
            node.text().strip() for node in root.descendants("a")
            if node.parent is not None and node.parent.has_class("manga-authors") and node.text().strip()
        )
        artists = [
            node.text().strip() for node in root.descendants("a")
            if node.parent is not None and node.parent.has_class("artist-content") and node.text().strip()
        ]
        genres = tuple(
            child.text().strip() for child in genres_box.children
            if isinstance(child, _Node) and child.tag == "a" and child.has_class("chip") and child.text().strip()
        ) if genres_box else ()
        source_id = str(response.url)
        return SourceSeries(
            source_id=source_id,
            title=self._own_text(title) if title else "",
            source_name=self.name,
            cover_url=_image_url(image, str(response.url)) if image else None,
            description="\n\n".join(paragraphs) or None,
            author=", ".join(authors) or None,
            artist=", ".join(artists) or None,
            status=status,
            content_tags=genres,
            web_url=source_id,
        )

    @staticmethod
    def _direct_child(node, predicate):
        return next((child for child in node.children if isinstance(child, _Node) and predicate(child)), None)

    @staticmethod
    def _class_ancestor(node, class_name):
        parent = node.parent
        while parent is not None:
            if parent.has_class(class_name):
                return parent
            parent = parent.parent
        return None

    @staticmethod
    def _own_text(node) -> str:
        return " ".join(child.strip() for child in node.children if isinstance(child, str) and child.strip())

    @staticmethod
    def _dragon_date(value: str) -> str | None:
        from calendar import monthrange
        from datetime import datetime, timedelta
        months = {
            "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
            "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10,
            "noviembre": 11, "diciembre": 12,
        }
        text = value.strip().lower()
        absolute = re.fullmatch(r"([^\s]+)\s+(\d{1,2}),\s*(\d{4})", text)
        if absolute and absolute.group(1) in months:
            return datetime(int(absolute.group(3)), months[absolute.group(1)], int(absolute.group(2))).isoformat()
        relative = re.search(r"(\d+)", text)
        if not text.startswith("hace") or relative is None:
            return None
        amount, now = int(relative.group()), datetime.now().replace(microsecond=0)
        if "dia" in text or "día" in text:
            result = now - timedelta(days=amount)
        elif "hora" in text:
            result = now - timedelta(hours=amount)
        elif "minuto" in text or " min" in text:
            result = now - timedelta(minutes=amount)
        elif "segundo" in text:
            result = now - timedelta(seconds=amount)
        elif "semana" in text:
            result = now - timedelta(days=amount * 7)
        elif "mes" in text:
            total = now.year * 12 + now.month - 1 - amount
            year, month = divmod(total, 12)
            result = now.replace(year=year, month=month + 1, day=min(now.day, monthrange(year, month + 1)[1]))
        elif "ano" in text or "año" in text:
            year = now.year - amount
            result = now.replace(year=year, day=min(now.day, monthrange(year, now.month)[1]))
        else:
            return None
        return result.isoformat()


class EmperorScanSource(DragonTranslationOrgSource):
    remove_premium_chapters = True

    def get_preferences(self) -> list[SourcePreference]:
        return [
            SourcePreference("random_user_agent", "Random user agent string", "select", [
                ("off", "OFF"), ("desktop", "Desktop"), ("mobile", "Mobile"),
            ], "off"),
            SourcePreference("custom_user_agent", "Custom user agent string", "text", default=""),
            SourcePreference(
                "removePremiumChapters", "Filtrar capítulos VIP", "checkbox",
                default=True,
            ),
        ]

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        script = _first(
            _parse_html(response.text),
            lambda node: node.tag == "script" and node.attrs.get("id") == "mk-chapters-data",
        )
        if script is None:
            raise ValueError("Emperor Scan no publico mk-chapters-data")
        payload = json.loads("".join(child for child in script.children if isinstance(child, str)))
        result: list[SourceChapter] = []
        for item in payload.get("items", []):
            title = str(item.get("name", "")).strip()
            url = str(item.get("url", "")).strip()
            if self.remove_premium_chapters and (
                any(value in title.lower() for value in ("vip", "soberano", "premium"))
                or "/membership-levels/" in url.lower()
                or "locked" in str(item.get("st", "")).lower()
            ):
                continue
            if not title or not url:
                continue
            number = re.search(r"\d+(?:\.\d+)?", title)
            result.append(SourceChapter(
                source_id=urljoin(str(response.url), url),
                title=title,
                series_id=series_id,
                source_name=self.name,
                number=float(number.group()) if number else None,
                language=self.language,
                uploaded_at=self._dragon_date(str(item.get("ago", ""))),
            ))
        return result

    def _dragon_details(self, response) -> SourceSeries:
        from dataclasses import replace
        series = super()._dragon_details(response)
        root = _parse_html(response.text)
        hcol = _first(root, lambda node: node.tag == "div" and node.has_class("hcol"))
        tags_box = self._direct_child(hcol, lambda node: node.has_class("hchips--tags")) if hcol else None
        categories = [*series.content_tags]
        if tags_box is not None:
            categories.extend(
                text for child in tags_box.children
                if isinstance(child, _Node) and child.tag == "a" and child.has_class("chip")
                and (text := child.text().strip()) and len(text) <= 25
                and "read" not in text.lower()
                and self.display_name.lower() not in text.lower()
                and series.title.lower() not in text.lower()
            )
        if self.remove_premium_chapters:
            categories = [
                item for item in categories
                if not any(value in item.lower() for value in ("vip", "premium", "emperor scan"))
            ]
        description = (series.description or "").replace(
            "HAZ CLICK AQUÍ PARA UNIRTE A NUESTRO DISCORD", "",
        ).strip() or None
        return replace(series, description=description, content_tags=tuple(categories))


class EsMi2MangaSource(DragonTranslationOrgSource):
    def __init__(self, fetcher=None) -> None:
        super().__init__(fetcher)
        self._load_more_detected = False

    async def browse(self, kind: str, page: int = 1):
        if kind not in {"popular", "latest"}:
            return {"items": [], "has_more": False}
        if self._load_more_detected:
            response = await self._request(
                "POST", f"{self.base_url}/wp-admin/admin-ajax.php",
                data=self._esmi_load_more(page, kind == "popular"),
            )
        else:
            path = "manga/" if page == 1 else f"manga/page/{page}/"
            response = await self._request(
                "GET", urljoin(f"{self.base_url}/", path),
                params={"m_orderby": "views" if kind == "popular" else "latest"},
            )
        response.raise_for_status()
        return self._esmi_page(response, search=False)

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        query = query.strip()
        if query.startswith("https://"):
            parsed = urlparse(query)
            if parsed.netloc != urlparse(self.base_url).netloc:
                raise ValueError("URL no compatible")
            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) < 2:
                raise ValueError("URL no compatible")
            query = f"slug:{parts[1]}"
        if query.startswith("slug:"):
            response = await self._request("GET", f"{self.base_url}/manga/{query[5:]}/")
            response.raise_for_status()
            return {"items": [self._esmi_details(response)], "has_more": False}
        values = filters or {}
        if self._load_more_detected:
            response = await self._request(
                "POST", f"{self.base_url}/wp-admin/admin-ajax.php",
                data=self._esmi_search_load_more(page, query, values),
            )
        else:
            path = "" if page == 1 else f"page/{page}/"
            params: list[tuple[str, str]] = [("s", query), ("post_type", "wp-manga")]
            for key, parameter in (("author", "author"), ("artist", "artist"), ("year", "release")):
                if str(values.get(key, "")).strip():
                    params.append((parameter, str(values[key]).strip()))
            if isinstance(values.get("status"), list):
                params.extend(("status[]", str(status)) for status in values["status"])
            for key, parameter in (("order", "m_orderby"), ("adult", "adult"), ("genre_condition", "op")):
                if key == "adult" or values.get(key):
                    params.append((parameter, str(values.get(key, ""))))
            if isinstance(values.get("genres"), list):
                params.extend(("genre[]", str(genre)) for genre in values["genres"])
            response = await self._request("GET", urljoin(f"{self.base_url}/", path), params=params)
        response.raise_for_status()
        return self._esmi_page(response, search=True)

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        series_url = urljoin(f"{self.base_url}/", series_id)
        response = await self._request("GET", series_url)
        response.raise_for_status()
        root = _parse_html(response.text)
        items = [node for node in root.descendants("li") if node.has_class("wp-manga-chapter")]
        holder = _first(root, lambda node: node.attrs.get("id", "").startswith("manga-chapters-holder"))
        if not items and holder is not None:
            chapter_response = await self._request(
                "POST", f"{self.base_url}/wp-admin/admin-ajax.php",
                data={"action": "manga_get_chapters", "manga": holder.attrs.get("data-id", "")},
            )
            if getattr(chapter_response, "status_code", 200) == 400:
                chapter_response = await self._request("POST", f"{series_url.rstrip('/')}/ajax/chapters")
            chapter_response.raise_for_status()
            items = [
                node for node in _parse_html(chapter_response.text).descendants("li")
                if node.has_class("wp-manga-chapter")
            ]
        result: list[SourceChapter] = []
        for item in items:
            anchor = _first(item, lambda node: node.tag == "a" and bool(node.attrs.get("href")))
            if anchor is None:
                continue
            title = anchor.text().strip()
            relative_image = _first(item, lambda node: node.tag == "img" and not node.has_class("thumb"))
            relative_link = _first(
                item,
                lambda node: node.tag == "a" and node.parent is not None
                and node.parent.tag == "span" and bool(node.attrs.get("title")),
            )
            date = _first(item, lambda node: node.tag == "span" and node.has_class("chapter-release-date"))
            date_text = (
                relative_image.attrs.get("alt", "") if relative_image is not None
                else relative_link.attrs.get("title", "") if relative_link is not None
                else date.text() if date else ""
            )
            chapter_url = urljoin(series_url, anchor.attrs["href"]).split("?style=paged", 1)[0]
            if not chapter_url.endswith(self.chapter_url_suffix):
                chapter_url += self.chapter_url_suffix
            number = re.search(r"\d+(?:\.\d+)?", title)
            result.append(SourceChapter(
                source_id=chapter_url,
                title=title or "Capítulo",
                series_id=series_id,
                source_name=self.name,
                number=float(number.group()) if number else None,
                language=self.language,
                uploaded_at=self._dragon_date(date_text),
            ))
        return result

    def _esmi_page(self, response, search: bool) -> dict:
        root = _parse_html(response.text)
        result: list[SourceSeries] = []
        for item in root.descendants("div"):
            if not self._has_class_ancestor(item, "site-content"):
                continue
            if search:
                if not item.has_class("c-tabs-item__content"):
                    continue
            elif (
                not item.has_class("page-item-detail") or not item.has_class("manga")
                or any("bilibilicomics.com" in node.attrs.get("href", "") for node in item.descendants("a"))
            ):
                continue
            title_box = _first(item, lambda node: node.has_class("post-title"))
            anchor = _first(title_box or item, lambda node: node.tag == "a" and bool(node.attrs.get("href")))
            if anchor is None:
                continue
            source_id = urljoin(str(response.url), anchor.attrs["href"])
            image = _first(item, lambda node: node.tag == "img")
            result.append(SourceSeries(
                source_id=source_id,
                title=self._own_text(anchor),
                source_name=self.name,
                cover_url=_image_url(image, str(response.url)) if image else None,
                web_url=source_id,
            ))
        root_has_no_posts = any(node.has_class("no-posts") for node in root.descendants())
        has_more = (
            not root_has_no_posts if self._load_more_detected
            else any(
                node.has_class("nav-previous") or node.has_class("navigation-ajax")
                or (node.tag == "a" and node.has_class("nextpostslink"))
                for node in root.descendants()
            )
        )
        if any(node.tag == "nav" and node.has_class("navigation-ajax") for node in root.descendants()):
            self._load_more_detected = True
        return {"items": result, "has_more": has_more}

    @staticmethod
    def _esmi_load_more(page: int, popular: bool) -> list[tuple[str, str]]:
        return [
            ("action", "madara_load_more"), ("page", str(page - 1)),
            ("template", "madara-core/content/content-archive"), ("vars[orderby]", "meta_value_num"),
            ("vars[paged]", "1"), ("vars[meta_query][0][key]", "_wp_manga_chapter_type"),
            ("vars[meta_query][0][value]", "manga"), ("vars[post_type]", "wp-manga"),
            ("vars[post_status]", "publish"),
            ("vars[meta_key]", "_wp_manga_views" if popular else "_latest_update"),
            ("vars[order]", "desc"), ("vars[sidebar]", "right"),
            ("vars[manga_archives_item_layout]", "big_thumbnail"),
        ]

    @staticmethod
    def _esmi_search_load_more(page: int, query: str, filters: dict) -> list[tuple[str, str]]:
        data: list[tuple[str, str]] = [
            ("action", "madara_load_more"), ("page", str(page - 1)),
            ("template", "madara-core/content/content-search"), ("vars[paged]", "1"),
            ("vars[template]", "archive"), ("vars[sidebar]", "right"),
            ("vars[post_type]", "wp-manga"), ("vars[post_status]", "publish"),
            ("vars[manga_archives_item_layout]", "big_thumbnail"),
            ("vars[meta_query][0][key]", "_wp_manga_chapter_type"),
            ("vars[meta_query][0][value]", "manga"), ("vars[s]", query),
        ]
        tax_index, meta_index = 0, 1
        for key, taxonomy in (("author", "wp-manga-author"), ("artist", "wp-manga-artist"), ("year", "wp-manga-release")):
            if str(filters.get(key, "")).strip():
                data.extend([
                    (f"vars[tax_query][{tax_index}][taxonomy]", taxonomy),
                    (f"vars[tax_query][{tax_index}][field]", "name"),
                    (f"vars[tax_query][{tax_index}][terms]", str(filters[key]).strip()),
                ])
                tax_index += 1
        statuses = filters.get("status", [])
        if isinstance(statuses, list) and statuses:
            data.append((f"vars[meta_query][{meta_index}][key]", "_wp_manga_status"))
            data.extend((f"vars[meta_query][{meta_index}][value][{index}]", str(status)) for index, status in enumerate(statuses))
            meta_index += 1
        order = str(filters.get("order", ""))
        order_values = {
            "latest": [("vars[orderby]", "meta_value_num"), ("vars[order]", "DESC"), ("vars[meta_key]", "_latest_update")],
            "alphabet": [("vars[orderby]", "post_title"), ("vars[order]", "ASC")],
            "rating": [("vars[orderby][query_average_reviews]", "DESC"), ("vars[orderby][query_total_reviews]", "DESC")],
            "trending": [("vars[orderby]", "meta_value_num"), ("vars[meta_key]", "_wp_manga_week_views_value"), ("vars[order]", "DESC")],
            "views": [("vars[orderby]", "meta_value_num"), ("vars[meta_key]", "_wp_manga_views"), ("vars[order]", "DESC")],
            "new-manga": [("vars[orderby]", "date"), ("vars[order]", "DESC")],
        }
        data.extend(order_values.get(order, []))
        adult = str(filters.get("adult", ""))
        if adult:
            data.extend([
                (f"vars[meta_query][{meta_index}][key]", "manga_adult_content"),
                (f"vars[meta_query][{meta_index}][compare]", "not exists" if adult == "0" else "exists"),
            ])
            meta_index += 1
        genres = filters.get("genres", [])
        if isinstance(genres, list) and genres:
            if filters.get("genre_condition") == "1":
                data.append((f"vars[tax_query][{tax_index}][operation]", "AND"))
            data.extend([
                (f"vars[tax_query][{tax_index}][taxonomy]", "wp-manga-genre"),
                (f"vars[tax_query][{tax_index}][field]", "slug"),
            ])
            data.extend((f"vars[tax_query][{tax_index}][terms][{index}]", str(genre)) for index, genre in enumerate(genres))
        return data

    def _esmi_details(self, response) -> SourceSeries:
        root = _parse_html(response.text)
        title = _first(root, lambda node: node.tag in {"h1", "h3"} and self._has_class_ancestor(node, "post-title"))
        image_box = _first(root, lambda node: node.has_class("summary_image"))
        image = _first(image_box, lambda node: node.tag == "img") if image_box else None
        description_box = _first(root, lambda node: node.has_class("summary__content") and self._has_class_ancestor(node, "description-summary"))
        authors = [node.text().strip() for node in root.descendants("a") if node.parent and node.parent.has_class("author-content")]
        artists = [node.text().strip() for node in root.descendants("a") if node.parent and node.parent.has_class("artist-content")]
        genres_box = _first(root, lambda node: node.has_class("genres-content"))
        genres = tuple(node.text().strip() for node in genres_box.descendants("a")) if genres_box else ()
        statuses = [node for node in root.descendants("div") if node.has_class("summary-content")]
        status_text = statuses[-1].text().strip().lower() if statuses else ""
        status = (
            "completed" if status_text in {"completed", "completo", "completado", "finalizado"}
            else "ongoing" if status_text in {"ongoing", "en curso", "emision", "publicandose", "publicándose"}
            else "hiatus" if status_text in {"on hold", "pausado", "en espera"}
            else "cancelled" if status_text in {"canceled", "cancelado"}
            else None
        )
        source_id = str(response.url)
        return SourceSeries(
            source_id=source_id,
            title=title.text().strip() if title else "",
            source_name=self.name,
            cover_url=_image_url(image, source_id) if image else None,
            description=description_box.text().strip() if description_box else None,
            author=", ".join(authors) or None,
            artist=", ".join(artists) or None,
            status=status,
            content_tags=genres,
            web_url=source_id,
        )


class HaremDeKiraSource(MadaraSource):
    async def browse(self, kind: str, page: int = 1):
        if kind not in {"popular", "latest"}:
            return {"items": [], "has_more": False}
        response = await self._request(
            "POST", f"{self.base_url}/wp-admin/admin-ajax.php",
            data=EsMi2MangaSource._esmi_load_more(page, kind == "popular"),
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        response.raise_for_status()
        return self._harem_page(response, search=False)

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        query = query.strip()
        if query.startswith("https://"):
            parsed = urlparse(query)
            if parsed.netloc != urlparse(self.base_url).netloc:
                raise ValueError("URL no compatible")
            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) < 2:
                raise ValueError("URL no compatible")
            query = f"slug:{parts[1]}"
        if query.startswith("slug:"):
            response = await self._request("GET", f"{self.base_url}/{self.manga_substring}/{query[5:]}/")
            response.raise_for_status()
            return {"items": [self._harem_details(response)], "has_more": False}
        response = await self._request(
            "POST", f"{self.base_url}/wp-admin/admin-ajax.php",
            data=EsMi2MangaSource._esmi_search_load_more(page, query, filters or {}),
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        response.raise_for_status()
        return self._harem_page(response, search=True)

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        return self._harem_details(response)

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        result: list[SourceChapter] = []
        for anchor in root.descendants("a"):
            if not (anchor.parent and anchor.parent.tag == "li" and self._has_id_ancestor(anchor, "list-chapters")):
                continue
            grid = _first(anchor, lambda node: node.tag == "div" and node.has_class("grid"))
            if grid is None:
                continue
            title_node = next(
                (child for child in grid.children if isinstance(child, _Node) and child.tag == "span"),
                None,
            )
            date_node = next(
                (child for child in grid.children if isinstance(child, _Node) and child.tag == "div"),
                None,
            )
            title = title_node.text().strip() if title_node else "Capítulo"
            number = re.search(r"\d+(?:\.\d+)?", title)
            result.append(SourceChapter(
                source_id=urljoin(str(response.url), anchor.attrs.get("href", "")),
                title=title,
                series_id=str(series_id),
                source_name=self.name,
                number=float(number.group()) if number else None,
                language=self.language,
                uploaded_at=self._madara_date(date_node.text()) if date_node else None,
            ))
        return result

    def _harem_page(self, response, search: bool) -> dict:
        root = _parse_html(response.text)
        containers = [
            node for node in root.descendants("div")
            if (
                search and node.has_class("grid") and node.parent is not None
                and node.parent.tag == "button" and node.parent.has_class("group")
            ) or (not search and node.has_class("latest-poster"))
        ]
        items: list[SourceSeries] = []
        for container in containers:
            title = _first(container, lambda node: node.tag == "h3" and bool(node.text().strip()))
            anchor = _first(container, lambda node: node.tag == "a" and bool(node.attrs.get("href")))
            styled = _first(
                container,
                lambda node: bool(node.attrs.get("style")) and node.has_class("bg-cover")
                and (node.tag == "div" if search else node.tag == "a"),
            )
            if title is None or anchor is None:
                continue
            source_id = urljoin(str(response.url), anchor.attrs["href"])
            items.append(SourceSeries(
                source_id=source_id,
                title=title.text().strip(),
                source_name=self.name,
                cover_url=self._style_image(styled.attrs["style"], str(response.url)) if styled else None,
                web_url=source_id,
            ))
        return {
            "items": items,
            "has_more": not any(node.has_class("no-posts") for node in root.descendants()),
        }

    def _harem_details(self, response) -> SourceSeries:
        root = _parse_html(response.text)
        title = _first(
            root,
            lambda node: node.tag == "h1" and node.parent is not None and node.parent.has_class("grid")
            and self._has_class_ancestor(node, "wp-manga"),
        )
        typed = [
            node for node in root.descendants("div")
            if node.attrs.get("alt") == "type" and self._has_class_ancestor(node, "wp-manga")
        ]
        status_node = _first(typed[0], lambda node: node.tag == "span") if typed else None
        genres = tuple(
            text for node in typed[1:]
            if (span := _first(node, lambda item: item.tag == "span")) is not None
            and (text := span.text().strip())
        )
        description = _first(
            root,
            lambda node: node.tag == "div" and node.attrs.get("id") == "expand_content"
            and self._has_class_ancestor(node, "wp-manga"),
        )
        paragraphs = description.descendants("p") if description else []
        description_text = (
            "\n\n".join(node.text().strip() for node in paragraphs if node.text().strip())
            if paragraphs else description.text().strip() if description else ""
        )
        image = _first(root, lambda node: node.tag == "img" and self._has_class_ancestor(node, "summary_image"))
        authors = self._detail_links(root, ("author-content", "manga-authors"))
        artists = self._detail_links(root, ("artist-content",))
        source_id = str(response.url)
        return SourceSeries(
            source_id=source_id,
            title=title.text().strip() if title else source_id.rstrip("/").rsplit("/", 1)[-1],
            source_name=self.name,
            cover_url=_image_url(image, source_id) if image else None,
            description=description_text or None,
            author=", ".join(authors) or None,
            artist=", ".join(artists) or None,
            status=self._madara_status(status_node.text() if status_node else ""),
            content_tags=genres,
            web_url=source_id,
        )

    @staticmethod
    def _style_image(style: str, base_url: str) -> str | None:
        found = re.search(r"url\((.*?)\)", style)
        return urljoin(base_url, found.group(1).strip(" '\"")) if found else None


class DoujinsHellSource(MadaraSource):
    def get_filters(self) -> list[SourceFilter]:
        return [
            SourceFilter("author", "Autor", "text", default=""),
            SourceFilter("artist", "Artista", "text", default=""),
            SourceFilter("year", "Ano de publicacion", "text", default=""),
            SourceFilter("status", "Estado", "multi_select", [
                ("end", "Completado"), ("on-going", "En curso"),
                ("canceled", "Cancelado"), ("on-hold", "En espera"),
            ], []),
            SourceFilter("order", "Ordenar por", "select", [
                ("", "Relevancia"), ("latest", "Mas recientes"), ("alphabet", "A-Z"),
                ("rating", "Valoracion"), ("trending", "Tendencia"),
                ("views", "Mas vistos"), ("new-manga", "Nuevos"),
            ], ""),
            SourceFilter("adult", "Contenido adulto", "select", [
                ("", "Todo"), ("0", "Excluir"), ("1", "Solo adulto"),
            ], ""),
        ]

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        values = filters or {}
        path = "" if page == 1 else f"page/{page}/"
        params: list[tuple[str, str]] = [("s", query), ("post_type", "wp-manga")]
        for key, parameter in (("author", "author"), ("artist", "artist"), ("year", "release")):
            if str(values.get(key, "")).strip():
                params.append((parameter, str(values[key]).strip()))
        statuses = values.get("status", [])
        if isinstance(statuses, list):
            params.extend(("status[]", str(status)) for status in statuses)
        if values.get("order"):
            params.append(("m_orderby", str(values["order"])))
        params.append(("adult", str(values.get("adult", ""))))
        response = await self._request("GET", urljoin(f"{self.base_url}/", path), params=params)
        response.raise_for_status()
        root = _parse_html(response.text)
        items = self._series_from_root(root, ("c-tabs-item__content", "manga__item"))
        has_more = any(
            node.attrs.get("rel") == "next" or node.has_class("nextpostslink")
            or node.has_class("nav-previous")
            for node in root.descendants()
        )
        return {"items": items, "has_more": has_more}

    @staticmethod
    def _doujinshell_date(value: str) -> str | None:
        from datetime import datetime
        months = {
            "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
            "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10,
            "noviembre": 11, "diciembre": 12,
        }
        found = re.fullmatch(r"(\d{1,2})\s+([^,]+),\s*(\d{4})", value.strip().lower())
        if not found or found.group(2) not in months:
            return None
        return datetime(int(found.group(3)), months[found.group(2)], int(found.group(1))).isoformat()

    @classmethod
    def _doujinshell_chapter_nodes(cls, root):
        return [
            node for node in root.descendants("li")
            if node.has_class("wp-manga-chapter") and cls._has_class_ancestor(node, "listing-chapters_wrap")
        ]

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        series_url = urljoin(f"{self.base_url}/", series_id)
        response = await self._request("GET", series_url)
        response.raise_for_status()
        root = _parse_html(response.text)
        items = self._doujinshell_chapter_nodes(root)
        holder = _first(root, lambda node: node.attrs.get("id", "").startswith("manga-chapters-holder"))
        if not items and holder is not None:
            chapter_response = await self._request(
                "POST", f"{self.base_url}/wp-admin/admin-ajax.php",
                data={"action": "manga_get_chapters", "manga": holder.attrs.get("data-id", "")},
            )
            if getattr(chapter_response, "status_code", 200) == 400:
                chapter_response = await self._request("POST", f"{series_url.rstrip('/')}/ajax/chapters")
            chapter_response.raise_for_status()
            items = self._doujinshell_chapter_nodes(_parse_html(chapter_response.text))
        result = []
        for item in items:
            anchor = _first(item, lambda node: node.tag == "a" and bool(node.attrs.get("href")))
            if anchor is None:
                continue
            title = anchor.text().strip()
            date = _first(item, lambda node: node.tag == "span" and node.has_class("chapter-release-date"))
            found = re.search(r"\d+(?:\.\d+)?", title)
            chapter_url = urljoin(series_url, anchor.attrs["href"]).split("?style=paged", 1)[0]
            if not chapter_url.endswith(self.chapter_url_suffix):
                chapter_url += self.chapter_url_suffix
            result.append(SourceChapter(
                source_id=chapter_url, title=title, series_id=series_id, source_name=self.name,
                number=float(found.group()) if found else None, language=self.language,
                uploaded_at=self._doujinshell_date(date.text()) if date else None,
            ))
        if len(result) == 1:
            only = result[0]
            result[0] = SourceChapter(
                source_id=only.source_id, title="Cap\u00edtulo", series_id=only.series_id,
                source_name=only.source_name, number=only.number, language=only.language,
                uploaded_at=only.uploaded_at,
            )
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        response = await self._request("GET", urljoin(f"{self.base_url}/", chapter_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        reading = _first(root, lambda node: node.has_class("reading-content"))
        images = [image for image in reading.descendants("img") if not image.has_class("aligncenter")] if reading else []
        if not images and reading and reading.descendants("iframe"):
            raise ValueError("No se admiten videos")
        urls = [_image_url(image, str(response.url)) for image in images]
        return [SourcePage(
            source_id=url, chapter_id=chapter_id, index=index,
            filename=urlparse(url).path.rsplit("/", 1)[-1] or f"{index}.jpg", source_name=self.name,
        ) for index, url in enumerate(urls)]

"""Fuente HTTP adaptable para extensiones sin un motor compartido."""

import asyncio
import ast
import base64
import hashlib
import io
import json
import re
import time
from datetime import datetime, timezone
from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlparse, urlunparse

from PIL import Image, ImageDraw, ImageFont

try:
    from .madara import (
        MadaraSource,
        SourceCapabilities,
        SourceChapter,
        SourceFilter,
        SourcePage,
        SourcePageContent,
        SourcePreference,
        SourceSeries,
        _Node,
        _first,
        _image_url,
        _parse_html,
    )
except ImportError:
    pass


def _gf_mul(left: int, right: int) -> int:
    result = 0
    while right:
        if right & 1:
            result ^= left
        left = ((left << 1) ^ (0x11B if left & 0x80 else 0)) & 0xFF
        right >>= 1
    return result


def _aes_sbox(value: int) -> int:
    inverse, base, exponent = 1, value, 254
    while exponent:
        if exponent & 1:
            inverse = _gf_mul(inverse, base)
        base = _gf_mul(base, base)
        exponent >>= 1
    if value == 0:
        inverse = 0
    return inverse ^ ((inverse << 1) | (inverse >> 7)) & 0xFF ^ ((inverse << 2) | (inverse >> 6)) & 0xFF ^ ((inverse << 3) | (inverse >> 5)) & 0xFF ^ ((inverse << 4) | (inverse >> 4)) & 0xFF ^ 0x63


_AES_SBOX = tuple(_aes_sbox(value) for value in range(256))
_AES_INV_SBOX = tuple(_AES_SBOX.index(value) for value in range(256))


def _aes256_decrypt(ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
    words = [list(key[index:index + 4]) for index in range(0, 32, 4)]
    rcon = 1
    for index in range(8, 60):
        temp = words[-1][:]
        if index % 8 == 0:
            temp = [_AES_SBOX[value] for value in temp[1:] + temp[:1]]
            temp[0] ^= rcon
            rcon = _gf_mul(rcon, 2)
        elif index % 8 == 4:
            temp = [_AES_SBOX[value] for value in temp]
        words.append([left ^ right for left, right in zip(words[index - 8], temp)])
    round_keys = [sum(words[index:index + 4], []) for index in range(0, 60, 4)]

    def decrypt_block(block: bytes) -> bytes:
        state = [value ^ key_value for value, key_value in zip(block, round_keys[14])]
        for round_number in range(13, -1, -1):
            state = [state[index] for index in (0, 13, 10, 7, 4, 1, 14, 11, 8, 5, 2, 15, 12, 9, 6, 3)]
            state = [_AES_INV_SBOX[value] for value in state]
            state = [value ^ key_value for value, key_value in zip(state, round_keys[round_number])]
            if round_number:
                mixed: list[int] = []
                for column in range(4):
                    a, b, c, d = state[column * 4:column * 4 + 4]
                    mixed.extend((
                        _gf_mul(a, 14) ^ _gf_mul(b, 11) ^ _gf_mul(c, 13) ^ _gf_mul(d, 9),
                        _gf_mul(a, 9) ^ _gf_mul(b, 14) ^ _gf_mul(c, 11) ^ _gf_mul(d, 13),
                        _gf_mul(a, 13) ^ _gf_mul(b, 9) ^ _gf_mul(c, 14) ^ _gf_mul(d, 11),
                        _gf_mul(a, 11) ^ _gf_mul(b, 13) ^ _gf_mul(c, 9) ^ _gf_mul(d, 14),
                    ))
                state = mixed
        return bytes(state)

    output = bytearray()
    previous = iv
    for index in range(0, len(ciphertext), 16):
        block = ciphertext[index:index + 16]
        output.extend(left ^ right for left, right in zip(decrypt_block(block), previous))
        previous = block
    padding = output[-1] if output else 0
    if not 1 <= padding <= 16 or output[-padding:] != bytes([padding]) * padding:
        raise ValueError("Relleno AES inválido")
    return bytes(output[:-padding])


def _cryptojs_decrypt(ciphertext: str, salt: str, password: str) -> str:
    generated = b""
    digest = b""
    password_bytes = password.encode()
    salt_bytes = bytes.fromhex(salt)
    while len(generated) < 48:
        digest = hashlib.md5(digest + password_bytes + salt_bytes).digest()
        generated += digest
    return _aes256_decrypt(base64.b64decode(ciphertext), generated[:32], generated[32:48]).decode()


def _safe_js_int(expression: str) -> int:
    operators = {ast.Add: int.__add__, ast.Sub: int.__sub__, ast.Mult: int.__mul__}

    def calculate(node) -> int:
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -calculate(node.operand)
        if isinstance(node, ast.BinOp) and type(node.op) in operators:
            return operators[type(node.op)](calculate(node.left), calculate(node.right))
        raise ValueError("Expresión JavaScript no soportada")

    return calculate(ast.parse(expression, mode="eval").body)


def _synchrony_keys(script: str) -> list[str]:
    direct = [match.group(2) for match in re.finditer(r'''decrypt\([^;]*?,\s*(['"])([^'"]+)\1\s*,''', script)]
    array_match = re.search(r"var\s+\w+\s*=\s*(\[.*?])\s*;\s*\w+\s*=\s*function", script, re.S)
    key_match = re.search(r"let\s+Gmacks\s*=\s*(.*?);", script)
    offset_match = re.search(r"\w+\s*=\s*\w+\s*-\s*(\([^;]+\));\s*var\s+\w+\s*=\s*\w+\[", script)
    if not all((array_match, key_match, offset_match)):
        return direct
    try:
        values = ast.literal_eval(array_match.group(1))
        offset = _safe_js_int(offset_match.group(1))
    except (SyntaxError, ValueError):
        return direct
    wrappers: dict[str, tuple[list[str], str]] = {}
    for match in re.finditer(r"function\s+(\w+)\(([^)]*)\)\{return\s+\w+\(([^,]+),[^)]*\);}", script):
        wrappers[match.group(1)] = ([value.strip() for value in match.group(2).split(",")], match.group(3))
    tokens: list[int | str] = []
    for match in re.finditer(r'''(\w+)\(([^)]*)\)|(['"])(.*?)\3''', key_match.group(1)):
        if match.group(4) is not None:
            tokens.append(match.group(4))
            continue
        wrapper = wrappers.get(match.group(1))
        if wrapper is None:
            return direct
        arguments = [value.strip() for value in match.group(2).split(",")]
        expression = wrapper[1]
        for name, value in zip(wrapper[0], arguments):
            expression = re.sub(rf"\b{re.escape(name)}\b", value, expression)
        try:
            tokens.append(_safe_js_int(expression) - offset)
        except ValueError:
            return direct
    for rotation in range(len(values)):
        rotated = values[rotation:] + values[:rotation]
        try:
            direct.append("".join(rotated[token] if isinstance(token, int) else token for token in tokens))
        except IndexError:
            continue
    return list(dict.fromkeys(direct))


class GenericSource(MadaraSource):
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


class MangaOniSource(GenericSource):
    status_options: tuple[tuple[str, str], ...] = ()
    type_options: tuple[tuple[str, str], ...] = ()
    genre_options: tuple[tuple[str, str], ...] = ()
    sort_options: tuple[tuple[str, str], ...] = ()

    def get_filters(self) -> list[SourceFilter]:
        return [
            SourceFilter("sort", "Ordenar por", "select", list(self.sort_options), "visitas"),
            SourceFilter("ascending", "Orden ascendente", "checkbox", default=False),
            SourceFilter("status", "Estado", "select", list(self.status_options), "false"),
            SourceFilter("type", "Tipo", "select", list(self.type_options), "false"),
            SourceFilter("genre", "Género", "select", list(self.genre_options), "false"),
            SourceFilter("adult", "Contenido +18", "select", [
                ("false", "Mostrar todo"), ("1", "Mostrar solo +18"), ("0", "No mostrar +18"),
            ], "false"),
        ]

    def get_preferences(self) -> list[SourcePreference]:
        return [SourcePreference(
            "hide_nsfw", "Ocultar contenido +18", "checkbox", default=False,
        )]

    async def browse(self, kind: str, page: int = 1):
        if kind == "popular":
            response = await self._request("GET", f"{self.base_url}/directorio", params={
                "genero": "false", "estado": "false", "filtro": "visitas",
                "tipo": "false", "adulto": "false", "orden": "desc", "p": str(page),
            })
            profile = "directory"
        elif kind == "latest":
            response = await self._request("GET", f"{self.base_url}/recientes", params={"p": str(page)})
            profile = "latest"
        else:
            return {"items": [], "has_more": False}
        response.raise_for_status()
        return self._oni_page(response, profile)

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        values = filters or {}
        if query.strip():
            path, params, profile = "/buscar", {"q": query.strip(), "p": str(page)}, "search"
        else:
            path, profile = "/directorio", "directory"
            params = {
                "adulto": str(values.get("adult", "false")),
                "estado": str(values.get("status", "false")),
                "tipo": str(values.get("type", "false")),
                "genero": str(values.get("genre", "false")),
                "filtro": str(values.get("sort", "visitas")),
                "orden": "asc" if values.get("ascending") else "desc",
                "p": str(page),
            }
        response = await self._request("GET", f"{self.base_url}{path}", params=params)
        response.raise_for_status()
        return self._oni_page(response, profile)

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        title = _first(root, lambda node: node.tag == "h1")
        if title is None:
            raise ValueError("MangaOni no publicó el título")
        cover = _first(root, lambda node: node.tag == "img" and "cover" in node.attrs.get("src", ""))
        synopses = [node for node in root.descendants("div") if node.attrs.get("id") == "sinopsis"]
        info = _first(root, lambda node: node.tag == "div" and node.attrs.get("id") == "info-i")
        info_text = info.text() if info else ""
        author = info_text.partition("Autor:")[2].partition("Fecha:")[0].strip() if "autor" in info_text.casefold() else "N/A"
        genres = tuple(
            node.text().strip() for node in root.descendants("a")
            if self._has_id_ancestor(node, "categ") and node.text().strip()
        )
        status = None
        for strong in root.descendants("strong"):
            if "estado" not in strong.text().casefold() or strong.parent is None:
                continue
            siblings = strong.parent.children
            status_node = next((node for node in siblings[siblings.index(strong) + 1:] if isinstance(node, _Node) and node.tag == "span"), None)
            value = status_node.text().strip() if status_node else ""
            status = "ongoing" if value == "En desarrollo" else "completed" if value == "Finalizado" else None
            break
        source_id = str(response.url)
        return SourceSeries(
            source_id=series_id,
            title=title.text().strip(),
            source_name=self.name,
            cover_url=_image_url(cover, source_id) if cover else None,
            description=self._own_text(synopses[-1]) or None if synopses else None,
            author=author,
            artist=author,
            status=status,
            content_tags=genres,
            web_url=source_id,
        )

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        result: list[SourceChapter] = []
        for anchor in _parse_html(response.text).descendants("a"):
            if not self._has_id_ancestor(anchor, "c_list") or not anchor.attrs.get("href"):
                continue
            span = _first(anchor, lambda node: node.tag == "span")
            number_text = span.attrs.get("data-num", "") if span else ""
            try:
                number = float(number_text)
            except ValueError:
                number = None
            try:
                uploaded_at = datetime.strptime(span.attrs.get("datetime", ""), "%Y-%m-%d %H:%M:%S").isoformat() if span else None
            except ValueError:
                uploaded_at = None
            result.append(SourceChapter(
                source_id=urljoin(str(response.url), anchor.attrs["href"]),
                title=anchor.text().strip(),
                series_id=series_id,
                source_name=self.name,
                number=number,
                language=self.language,
                uploaded_at=uploaded_at,
            ))
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else str(chapter)
        response = await self._request("GET", urljoin(f"{self.base_url}/", chapter_id))
        response.raise_for_status()
        script = next((node.text() for node in _parse_html(response.text).descendants("script") if "unicap" in node.text()), "")
        match = re.search(r"unicap[^']*'([^']+)'", script)
        if match is None:
            raise ValueError("MangaOni no publicó unicap")
        encoded = match.group(1)
        decoded = base64.b64decode(encoded[:len(encoded) - len(encoded) % 4]).decode()
        path, _, tail = decoded.partition("||")
        files = json.loads(tail)
        return [SourcePage(
            source_id=urljoin(str(response.url), path + str(filename)),
            chapter_id=chapter_id,
            index=index,
            filename=str(filename).rsplit("/", 1)[-1] or f"{index}.jpg",
            source_name=self.name,
        ) for index, filename in enumerate(files, 1)]

    def _oni_page(self, response, profile: str) -> dict:
        root = _parse_html(response.text)
        if profile == "latest":
            containers = [node for node in root.descendants("div") if node.has_class("_1bJU3")]
        elif profile == "search":
            article = _first(root, lambda node: node.attrs.get("id") == "article-div")
            containers = [node for node in article.children if isinstance(node, _Node) and node.tag == "div"] if article else []
        else:
            containers = [node for node in root.descendants("a") if self._has_id_ancestor(node, "article-div")]
        items: list[SourceSeries] = []
        for container in containers:
            anchor = container if container.tag == "a" else _first(
                container,
                lambda node: node.tag == "a" and bool(node.attrs.get("href"))
                and (profile != "latest" or node.attrs.get("data-test") == "latest-update-name"),
            )
            if anchor is None or not anchor.attrs.get("href"):
                continue
            divs = container.descendants("div")
            title = anchor.text().strip() if profile != "directory" else (divs[1].text().strip() if len(divs) > 1 else anchor.text().strip())
            image = _first(container, lambda node: node.tag == "img")
            source_id = urljoin(str(response.url), anchor.attrs["href"])
            items.append(SourceSeries(
                source_id=source_id,
                title=title,
                source_name=self.name,
                cover_url=_image_url(image, str(response.url)) if image else None,
                web_url=source_id,
            ))
        has_more = any(node.tag == "a" and node.attrs.get("rel") == "next" and self._has_class_ancestor(node, "pagination") for node in root.descendants("a"))
        return {"items": items, "has_more": has_more}

    @staticmethod
    def _own_text(node: _Node) -> str:
        return " ".join(child.strip() for child in node.children if isinstance(child, str) and child.strip())


class MangasInSource(GenericSource):
    item_path = "manga"

    def __init__(self, fetcher=None) -> None:
        super().__init__(fetcher)
        self.capabilities.headers["Referer"] = f"{self.base_url}/"
        self._chapter_key = ""

    async def browse(self, kind: str, page: int = 1):
        if kind == "popular":
            response = await self._request(
                "GET", f"{self.base_url}/filterList",
                params={"page": str(page), "sortBy": "views", "asc": "false"},
            )
            response.raise_for_status()
            return self._mmr_page(response)
        if kind == "latest":
            response = await self._request("GET", f"{self.base_url}/lasted", params={"p": str(page)})
            response.raise_for_status()
            payload = self._json(response)
            items = [self._suggestion(item.get("manga_slug"), item.get("manga_name")) for item in payload.get("data", [])]
            return {"items": [item for item in items if item], "has_more": page < int(payload.get("totalPages", 1))}
        return {"items": [], "has_more": False}

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        if query.strip():
            response = await self._request("GET", f"{self.base_url}/search", params={"q": query.strip()})
            response.raise_for_status()
            values = self._json(response)
            start = (page - 1) * 24
            items = [self._suggestion(item.get("data"), item.get("value")) for item in values]
            items = [item for item in items if item]
            return {"items": items[start:start + 24], "has_more": start + 24 < len(items)}
        params = {"page": str(page)}
        for key in ("cat", "alpha", "tag", "sortBy", "asc"):
            if (filters or {}).get(key) not in {None, ""}:
                params[key] = str(filters[key])
        response = await self._request("GET", f"{self.base_url}/filterList", params=params)
        response.raise_for_status()
        return self._mmr_page(response)

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        title = _first(root, lambda node: node.has_class("listmanga-header") or node.has_class("widget-title"))
        if title is None:
            raise ValueError("Mangas.in no publicó el título")
        image = _first(root, lambda node: node.tag == "img" and self._has_class_ancestor(node, "row"))
        wells = [node for node in root.descendants() if node.has_class("well") and self._has_class_ancestor(node, "row")]
        author = artist = ""
        genres: tuple[str, ...] = ()
        status = None
        for term in root.descendants("dt"):
            value = self._next_element(term)
            label = term.text().strip().casefold().rstrip(":")
            if value is None:
                continue
            if label in {"author(s)", "autor(es)", "autor"}:
                author = value.text().strip()
            elif label in {"artist(s)", "artista(s)", "artista"}:
                artist = value.text().strip()
            elif label in {"categories", "categorías", "categorias", "género"}:
                genres = tuple(node.text().strip() for node in value.descendants("a") if node.text().strip())
            elif label in {"status", "estado"}:
                normalized = value.text().strip().casefold()
                status = "completed" if normalized in {"complete", "completo", "finalizado"} else "ongoing" if normalized in {"ongoing", "activo"} else "cancelled" if normalized == "dropped" else None
        source_id = str(response.url)
        return SourceSeries(
            source_id=series_id,
            title=title.text().strip(),
            source_name=self.name,
            cover_url=self._guess_cover(source_id, _image_url(image, source_id) if image else None),
            description="\n".join(node.text().strip() for node in wells if node.text().strip()) or None,
            author=author or None,
            artist=artist or None,
            status=status,
            content_tags=genres,
            web_url=source_id,
        )

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        escaped = re.search(r'''\{(?=[^{}]*\\?["']ct\\?["'])(?=[^{}]*\\?["']s\\?["'])[^{}]+}''', response.text)
        if escaped is None:
            raise ValueError("Mangas.in no publicó la lista de capítulos")
        encoded = escaped.group().replace(r'\"', '"').replace(r"\'", "'")
        chapter_data = json.loads(encoded)
        keys = [self._chapter_key] if self._chapter_key else []
        if not keys:
            key_response = await self._request("GET", f"{self.base_url}/js/ads2.js")
            key_response.raise_for_status()
            keys = _synchrony_keys(key_response.text)
        chapters = None
        for key in keys:
            try:
                decrypted = _cryptojs_decrypt(chapter_data["ct"], chapter_data["s"], key)
                value = json.loads(decrypted)
                while isinstance(value, str):
                    value = json.loads(value)
                if isinstance(value, list):
                    chapters, self._chapter_key = value, key
                    break
            except (KeyError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
                continue
        if chapters is None:
            raise ValueError("Mangas.in no pudo descifrar los capítulos")
        manga_url = str(response.url).rstrip("/")
        result: list[SourceChapter] = []
        for item in chapters:
            number_text = str(item.get("number", ""))
            name = str(item.get("name", "")).strip()
            default_name = f"Capítulo {number_text}"
            try:
                number = float(number_text)
            except ValueError:
                number = None
            try:
                uploaded_at = datetime.strptime(str(item.get("created_at", "")), "%Y-%m-%d %H:%M:%S").isoformat()
            except ValueError:
                uploaded_at = None
            result.append(SourceChapter(
                source_id=f"{manga_url}/{item.get('slug', '')}",
                title=name if name == default_name else f"{default_name}: {name}",
                series_id=series_id,
                source_name=self.name,
                number=number,
                language=self.language,
                uploaded_at=uploaded_at,
            ))
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else str(chapter)
        response = await self._request("GET", urljoin(f"{self.base_url}/", chapter_id))
        response.raise_for_status()
        urls: list[str] = []
        for image in _parse_html(response.text).descendants("img"):
            if not image.has_class("img-responsive") or image.parent is None or image.parent.attrs.get("id") != "all":
                continue
            url = _image_url(image, str(response.url))
            if urlparse(url).scheme not in {"http", "https"} and "://" in url:
                url = unquote(base64.b64decode(url.partition("://")[2]).decode())
            urls.append(url)
        return [SourcePage(
            source_id=url,
            chapter_id=chapter_id,
            index=index,
            filename=urlparse(url).path.rsplit("/", 1)[-1] or f"{index}.jpg",
            source_name=self.name,
        ) for index, url in enumerate(urls, 1)]

    def _mmr_page(self, response) -> dict:
        root = _parse_html(response.text)
        items: list[SourceSeries] = []
        for container in root.descendants("div"):
            if not container.has_class("media"):
                continue
            heading = _first(container, lambda node: node.has_class("media-heading") or node.has_class("manga-heading"))
            anchor = _first(heading or container, lambda node: node.tag == "a" and bool(node.attrs.get("href")))
            if anchor is None:
                continue
            source_id = urljoin(str(response.url), anchor.attrs["href"])
            image = _first(container, lambda node: node.tag == "img")
            items.append(SourceSeries(
                source_id=source_id,
                title=anchor.text().strip(),
                source_name=self.name,
                cover_url=self._guess_cover(source_id, _image_url(image, str(response.url)) if image else None),
                web_url=source_id,
            ))
        has_more = any(node.tag == "a" and node.attrs.get("rel") == "next" and self._has_class_ancestor(node, "pagination") for node in root.descendants("a"))
        return {"items": items, "has_more": has_more}

    def _suggestion(self, slug, title) -> SourceSeries | None:
        if not slug or not title:
            return None
        source_id = f"{self.base_url}/{self.item_path}/{slug}"
        return SourceSeries(
            source_id=source_id,
            title=str(title),
            source_name=self.name,
            cover_url=self._guess_cover(source_id, None),
            web_url=source_id,
        )

    def _guess_cover(self, manga_url: str, image_url: str | None) -> str:
        return image_url if image_url and not image_url.endswith("no-image.png") else f"{self.base_url}/uploads/manga/{manga_url.rstrip('/').rsplit('/', 1)[-1]}/cover/cover_250x350.jpg"

    @staticmethod
    def _json(response):
        return response.json() if hasattr(response, "json") else json.loads(response.text)

    @staticmethod
    def _next_element(node: _Node) -> _Node | None:
        if node.parent is None:
            return None
        siblings = node.parent.children
        return next((item for item in siblings[siblings.index(node) + 1:] if isinstance(item, _Node)), None)


class DragonBallMultiverseSource(GenericSource):
    supports_latest = False
    _internal_languages = {
        "ja": "jp", "zh": "cn", "tr": "tr_TR", "pt-BR": "pt_BR",
        "hu": "hu_HU", "ga": "ga_ES", "ca": "ct_CT", "no": "no_NO",
        "ru": "ru_RU", "ro": "ro_RO", "eu": "eu_EH", "lt": "lt_LT",
        "hr": "hr_HR", "ko": "kr_KR", "fi": "fi_FI", "he": "he_HE",
        "bg": "bg_BG", "sv": "sv_SE", "el": "gr_GR", "es-419": "es_CO",
        "ar": "ar_JO", "fil": "tl_PI", "la": "la_LA", "da": "da_DK",
        "co": "co_FR", "br": "br_FR", "vec": "xx_VE", "lmo": "xx_LMO",
    }

    def __init__(self, fetcher=None) -> None:
        super().__init__(fetcher)
        self.capabilities = SourceCapabilities(
            search=False,
            browse=True,
            headers=self.capabilities.headers,
            requests_per_minute=self.requests_per_minute,
            content_warning=self.content_warning,
        )

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        return []

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind != "popular":
            return []
        language = self._internal_languages.get(self.language, self.language)
        response = await self._request("GET", f"{self.base_url}/{language}/read.html")
        response.raise_for_status()
        root = _parse_html(response.text)
        result: list[SourceSeries] = []
        for item in root.descendants():
            if not item.has_class("dbm-read") or not self._has_id_ancestor(item, "dbm-reads"):
                continue
            title = _first(item, lambda node: node.tag == "h3")
            anchor = _first(item, lambda node: node.tag == "a" and bool(node.attrs.get("href")))
            if title is None or anchor is None:
                continue
            source_id = urljoin(str(response.url), anchor.attrs["href"])
            image = _first(item, lambda node: node.tag == "img")
            description = next(
                (child.text().strip() for child in item.children if isinstance(child, _Node) and child.tag == "div"),
                "",
            )
            result.append(SourceSeries(
                source_id=source_id,
                title=title.text().strip(),
                source_name=self.name,
                cover_url=_image_url(image, str(response.url)) if image else None,
                description=description or None,
                web_url=source_id,
            ))
        return result

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        result: list[SourceChapter] = []
        for item in _parse_html(response.text).descendants():
            if not item.has_class("cadrelect") or not item.has_class("chapter"):
                continue
            anchor = _first(item, lambda node: node.tag == "a" and bool(node.attrs.get("href")))
            title = _first(item, lambda node: node.tag == "h4")
            if anchor is not None and title is not None:
                result.append(SourceChapter(
                    source_id=urljoin(str(response.url), anchor.attrs["href"]),
                    title=title.text().strip(),
                    series_id=series_id,
                    source_name=self.name,
                    language=self.language,
                ))
        return list(reversed(result))

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        response = await self._request("GET", urljoin(f"{self.base_url}/", chapter_id))
        response.raise_for_status()
        anchors = [
            node for node in _parse_html(response.text).descendants("a")
            if node.attrs.get("href") and self._has_class_ancestor(node, "pageslist")
        ]
        result: list[SourcePage] = []
        for index, anchor in enumerate(anchors, 1):
            page = await self._request("GET", urljoin(str(response.url), anchor.attrs["href"]))
            page.raise_for_status()
            image_url = self._image_with_balloons(page)
            parsed = urlparse(image_url)
            result.append(SourcePage(
                source_id=image_url,
                chapter_id=chapter_id,
                index=index,
                filename=parsed.path.rsplit("/", 1)[-1] or f"{index}.jpg",
                source_name=self.name,
            ))
        return result

    async def page_bytes(self, page: SourcePage | str) -> SourcePageContent:
        url = page.source_id if isinstance(page, SourcePage) else page
        parsed = urlparse(url)
        if not parsed.fragment:
            return await super().page_bytes(page)
        headers = dict(self.image_headers)
        if isinstance(page, SourcePage):
            headers.setdefault("Referer", page.chapter_id)
        response = await self._request("GET", urlunparse(parsed._replace(fragment="")), headers=headers)
        response.raise_for_status()
        layout = json.loads(unquote(parsed.fragment))
        image = Image.open(io.BytesIO(response.content)).convert("RGB")
        draw = ImageDraw.Draw(image)
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", 14)
        except OSError:
            font = ImageFont.load_default()
        for balloon in layout["balloons"]:
            scale = float(layout["scale"])
            x, y = float(balloon["left"]) * scale, float(balloon["top"]) * scale
            width = max(1, int(float(balloon["width"]) * scale))
            lines = self._wrap_text(draw, str(balloon["text"]), font, width)
            draw.multiline_text((x + width / 2, y), "\n".join(lines), fill="black", font=font, anchor="ma", align="center")
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=95)
        return SourcePageContent(media_type="image/jpeg", chunks=iter([buffer.getvalue()]))

    def _image_with_balloons(self, response) -> str:
        element = _first(_parse_html(response.text), lambda node: node.attrs.get("id") == "balloonsimg")
        if element is None:
            raise ValueError("La pagina no contiene #balloonsimg")
        image = element if element.attrs.get("src") else _first(element, lambda node: node.tag == "img")
        raw_url = image.attrs.get("src", "") if image is not None else ""
        if not raw_url:
            match = re.search(r"url\(\s*(['\"]?)(.*?)\1\s*\)", element.attrs.get("style", ""), re.I)
            raw_url = match.group(2) if match else ""
        if not raw_url:
            raise ValueError("#balloonsimg no contiene una imagen")
        raw_url = urljoin(str(response.url), raw_url)
        balloons = []
        for node in element.descendants():
            if not node.has_class("balloon"):
                continue
            style = node.attrs.get("style", "")
            balloons.append({
                "text": node.text(),
                "left": self._css_number(style, "left"),
                "top": self._css_number(style, "top"),
                "width": self._css_number(style, "width"),
            })
        if not balloons:
            return raw_url
        scale = re.search(r"scale\(\s*([\d.]+)", element.attrs.get("style", ""), re.I)
        layout = {"scale": float(scale.group(1)) if scale else 1.0, "balloons": balloons}
        return f"{raw_url}#{quote(json.dumps(layout, separators=(',', ':')), safe='')}"

    @staticmethod
    def _css_number(style: str, prop: str) -> float:
        match = re.search(rf"(?:^|;)\s*{re.escape(prop)}\s*:\s*([^;]+)", style, re.I)
        number = re.search(r"[\d.]+", match.group(1)) if match else None
        return float(number.group()) if number else 0.0

    @staticmethod
    def _has_id_ancestor(node: _Node, element_id: str) -> bool:
        parent = node.parent
        while parent is not None:
            if parent.attrs.get("id") == element_id:
                return True
            parent = parent.parent
        return False

    @staticmethod
    def _wrap_text(draw, text: str, font, width: int) -> list[str]:
        lines: list[str] = []
        for paragraph in text.splitlines() or [""]:
            line = ""
            for word in paragraph.split():
                candidate = f"{line} {word}".strip()
                if not line or draw.textlength(candidate, font=font) <= width:
                    line = candidate
                else:
                    lines.append(line)
                    line = word
            lines.append(line)
        return lines


class DynastySource(GenericSource):
    extra_headers = {"Accept": "application/json, text/plain, */*"}
    image_headers = extra_headers

    def get_filters(self) -> list[SourceFilter]:
        return [
            SourceFilter("sort", "Ordenar por", "select", [
                ("newest", "Recientes"), ("popular", "Populares"),
                ("rating", "Valorados"), ("az", "A - Z"),
            ], "newest"),
            SourceFilter("genre", "Género (Ignorado si hay texto en la búsqueda)", "select", [
                ("", "Todos"), ("accion", "Acción"), ("artes-marciales", "Artes Marciales"),
                ("aventura", "Aventura"), ("bl", "BL"), ("ciencia-ficcion", "Ciencia Ficción"),
                ("comedia", "Comedia"), ("deportes", "Deportes"), ("drama", "Drama"),
                ("ecchi", "Ecchi"), ("escolar", "Escolar"), ("fantasia", "Fantasía"),
                ("harem", "Harem"), ("humanity-fvck-yeah", "HFY"), ("horror", "Horror"),
                ("isekai", "Isekai"), ("kingdom-building", "Kingdom building"), ("mecha", "Mecha"),
                ("misterio", "Misterio"), ("murim", "Murim"), ("psicologico", "Psicológico"),
                ("reencarnacion", "Reencarnación"), ("romance", "Romance"), ("seinen", "Seinen"),
                ("shounen", "Shounen"), ("sistemas", "Sistemas"), ("slice-of-life", "Slice of Life"),
                ("sobrenatural", "Sobrenatural"), ("tragedia", "Tragedia"), ("xianxia", "Xianxia"),
                ("xuanhuan", "Xuanhuan"), ("yuri", "Yuri"),
            ], ""),
        ]

    async def browse(self, kind: str, page: int = 1):
        sort = {"popular": "popular", "latest": "newest"}.get(kind)
        if sort is None:
            return {"items": [], "has_more": False}
        return await self._manga_page(page, sort)

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        values = filters or {}
        sort = str(values.get("sort", "newest"))
        params = {"page": str(page), "limit": "20", "sort": sort}
        if query.strip():
            params["search"] = query.strip()
        elif values.get("genre"):
            params["genre"] = str(values["genre"])
        return await self._manga_page(page, sort, params)

    async def _manga_page(self, page: int, sort: str, params: dict | None = None):
        response = await self._request(
            "GET", f"{self.base_url}/api/mangas",
            params=params or {"page": str(page), "limit": "20", "sort": sort},
        )
        response.raise_for_status()
        payload = self._json(response)
        data = [item for item in payload.get("data", []) if "novel" not in str(item.get("type", "")).lower()]
        if sort == "popular":
            data.sort(key=lambda item: item.get("views") or 0, reverse=True)
        elif sort == "newest":
            data.sort(key=lambda item: self._date_timestamp(str(item.get("updated_at", ""))), reverse=True)
        elif sort == "rating":
            data.sort(key=lambda item: item.get("rating") or 0, reverse=True)
        elif sort == "az":
            data.sort(key=lambda item: str(item.get("title", "")))
        return {
            "items": [self._manga(item) for item in data],
            "has_more": page < int(payload.get("totalPages", 1)),
        }

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        manga_id = str(series_id).split("|", 1)[0]
        result: list[SourceChapter] = []
        page, total_pages = 1, 1
        while page <= total_pages:
            response = await self._request(
                "GET", f"{self.base_url}/api/chapters/paginated",
                params={"manga_id": manga_id, "page": str(page), "limit": "100", "sort": "desc"},
            )
            response.raise_for_status()
            payload = self._json(response)
            total_pages = int(payload.get("totalPages", 1))
            for item in payload.get("chapters", []):
                number = item.get("number")
                title = str(item.get("title") or "").strip()
                name = f"Capítulo {float(number):g}" if number is not None else ""
                if title and title != "null":
                    name = f"{name} - {title}" if name else title
                result.append(SourceChapter(
                    source_id=str(item["id"]),
                    title=name or "Capítulo",
                    series_id=str(series_id),
                    source_name=self.name,
                    number=float(number) if number is not None else None,
                    language=self.language,
                    uploaded_at=self._date_iso(str(item.get("created_at", ""))),
                ))
            page += 1
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        response = await self._request(
            "GET", f"{self.base_url}/api/chapter-pages", params={"chapter_id": str(chapter_id)},
        )
        response.raise_for_status()
        return [
            SourcePage(
                source_id=str(item["image_url"]),
                chapter_id=str(chapter_id),
                index=index,
                filename=urlparse(str(item["image_url"])).path.rsplit("/", 1)[-1] or f"{index}.jpg",
                source_name=self.name,
            )
            for index, item in enumerate(self._json(response), 1)
        ]

    def _manga(self, item: dict) -> SourceSeries:
        source_id = f"{item['id']}|{item['slug']}"
        kind = str(item.get("type") or "")
        cover = item.get("cover_image")
        return SourceSeries(
            source_id=source_id,
            title=str(item.get("title", "")),
            source_name=self.name,
            cover_url=urljoin(f"{self.base_url}/", str(cover)) if cover else None,
            description=item.get("description"),
            author=str(item.get("author") or "").strip() or None,
            artist=str(item.get("artist") or "").strip() or None,
            status={"ongoing": "ongoing", "completed": "completed", "hiatus": "hiatus"}.get(item.get("status")),
            content_tags=((kind[:1].upper() + kind[1:]),) if kind else (),
            web_url=f"{self.base_url}/manga/{item['slug']}" if item.get("slug") else self.base_url,
        )

    @staticmethod
    def _json(response):
        try:
            return response.json()
        except AttributeError:
            return json.loads(response.text)

    @staticmethod
    def _date_timestamp(value: str) -> float:
        from datetime import datetime, timezone
        try:
            return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            return 0.0

    @classmethod
    def _date_iso(cls, value: str) -> str | None:
        from datetime import datetime, timezone
        try:
            return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            return None


class EnchiladaScanSource(GenericSource):
    supports_latest = False

    def __init__(self, fetcher=None) -> None:
        super().__init__(fetcher)
        self._catalog: list[dict] = []
        self._catalog_lock = asyncio.Lock()

    async def browse(self, kind: str, page: int = 1):
        if kind != "popular":
            return {"items": [], "has_more": False}
        return {"items": [self._catalog_manga(item) for item in await self._fetch_catalog()], "has_more": False}

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        needle = query.casefold()
        items = [
            self._catalog_manga(item) for item in await self._fetch_catalog()
            if needle in str(item.get("title", "")).casefold()
        ]
        return {"items": items, "has_more": False}

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        response = await self._request("GET", f"{self.base_url}{series_id}")
        response.raise_for_status()
        root = _parse_html(response.text)
        holder = _first(root, lambda node: node.tag == "ul" and node.attrs.get("id") == "chaptersList")
        result: list[SourceChapter] = []
        if holder is not None:
            for item in holder.children:
                if not isinstance(item, _Node) or item.tag != "li":
                    continue
                anchor = _first(item, lambda node: node.tag == "a" and bool(node.attrs.get("href")))
                title = _first(item, lambda node: node.has_class("cap-title"))
                if anchor is None or title is None:
                    continue
                absolute = urljoin(str(response.url), anchor.attrs["href"])
                parsed = urlparse(absolute)
                source_id = urlunparse(("", "", parsed.path, parsed.params, parsed.query, ""))
                number = re.search(r"\d+(?:\.\d+)?", title.text())
                result.append(SourceChapter(
                    source_id=source_id,
                    title=title.text().strip(),
                    series_id=str(series_id),
                    source_name=self.name,
                    number=float(number.group()) if number else None,
                    language=self.language,
                ))
        return list(reversed(result))

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        parsed = urlparse(str(chapter_id).rstrip("/"))
        segments = [part for part in parsed.path.split("/") if part]
        if len(segments) < 2:
            raise ValueError("Ruta de capítulo de EnchiladaScan no válida")
        manga_slug, chapter_slug = segments[-2:]
        response = await self._request(
            "GET", f"{self.base_url}/assets/mangas/{manga_slug}/{chapter_slug}/images.json",
        )
        response.raise_for_status()
        values = response.json() if hasattr(response, "json") else json.loads(response.text)
        return [SourcePage(
            source_id=str(url),
            chapter_id=str(chapter_id),
            index=index,
            filename=urlparse(str(url)).path.rsplit("/", 1)[-1] or f"{index}.jpg",
            source_name=self.name,
        ) for index, url in enumerate(values, 1)]

    async def page_bytes(self, page: SourcePage | str) -> SourcePageContent:
        url = page.source_id if isinstance(page, SourcePage) else page
        response = await self._request("GET", url, headers={"Referer": f"{self.base_url}/"})
        response.raise_for_status()
        return SourcePageContent(
            media_type=response.headers.get("Content-Type", "image/jpeg"),
            chunks=iter([response.content]),
        )

    async def _fetch_catalog(self) -> list[dict]:
        if self._catalog:
            return self._catalog
        async with self._catalog_lock:
            if self._catalog:
                return self._catalog
            response = await self._request("GET", f"{self.base_url}/catalogo.json")
            response.raise_for_status()
            payload = response.json() if hasattr(response, "json") else json.loads(response.text)
            self._catalog = list(payload.get("items", []))
        return self._catalog

    def _catalog_manga(self, item: dict) -> SourceSeries:
        source_id = str(item.get("post_url", ""))
        cover = str(item.get("portada", ""))
        return SourceSeries(
            source_id=source_id,
            title=str(item.get("title", "")),
            source_name=self.name,
            cover_url=f"{self.base_url}{cover}" if cover else None,
            web_url=f"{self.base_url}{source_id}",
        )


class IkigaiMangasSource(GenericSource):
    sort_options: tuple[tuple[str, str], ...] = ()
    status_options: tuple[tuple[str, str], ...] = ()
    genre_options: tuple[tuple[str, str], ...] = ()

    def get_preferences(self) -> list[SourcePreference]:
        return [SourcePreference("show_nsfw", "Mostrar contenido NSFW", "checkbox", default=False)]

    def get_filters(self) -> list[SourceFilter]:
        return [
            SourceFilter("sort", "Ordenar por", "select", list(self.sort_options), "last_chapter_date"),
            SourceFilter("direction", "Dirección", "select", [("desc", "Descendente"), ("asc", "Ascendente")], "desc"),
            SourceFilter("statuses", "Estados", "multi_select", list(self.status_options), []),
            SourceFilter("genres", "Géneros", "multi_select", list(self.genre_options), []),
        ]

    async def browse(self, kind: str, page: int = 1):
        if kind == "popular":
            response = await self._request("GET", f"{self.base_url}/clasificacion/", headers=self._headers())
        elif kind == "latest":
            response = await self._request("GET", f"{self.base_url}/", params={"pagina": str(page)}, headers=self._headers())
        else:
            return {"items": [], "has_more": False}
        response.raise_for_status()
        return {
            "items": self._cards(response, "popular" if kind == "popular" else "latest"),
            "has_more": False if kind == "popular" else self._has_next(response.text),
        }

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        if query.startswith("http"):
            parsed = urlparse(query)
            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) < 2 or parts[-2] != "series":
                raise ValueError("URL de Ikigai Mangas no válida")
            return {"items": [await self.details(parts[-1])], "has_more": False}
        values = filters or {}
        params: list[tuple[str, str]] = [("tipos[]", "comic"), ("pagina", str(page))]
        params.extend(("generos[]", str(value)) for value in values.get("genres", []))
        params.extend(("estados[]", str(value)) for value in values.get("statuses", []))
        params.extend([
            ("ordenar", str(values.get("sort", "last_chapter_date"))),
            ("direccion", str(values.get("direction", "desc"))),
        ])
        response = await self._request("GET", f"{self.base_url}/series/", params=params, headers=self._headers())
        response.raise_for_status()
        items = self._cards(response, "search")
        if query.strip():
            # ponytail: Qwik has no stable text endpoint; scan each requested page until it exposes one.
            needle = query.strip().casefold()
            items = [item for item in items if needle in item.title.casefold()]
        return {"items": items, "has_more": self._has_next(response.text)}

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        slug = self._slug(series.source_id if isinstance(series, SourceSeries) else str(series))
        response = await self._request("GET", f"{self.base_url}/series/{slug}/", headers=self._headers())
        response.raise_for_status()
        root = _parse_html(response.text)
        article = _first(root, lambda node: node.tag == "article" and node.has_class("card"))
        if article is None:
            raise ValueError("Ficha de Ikigai Mangas no encontrada")
        title = _first(article, lambda node: node.has_class("card-title"))
        image = _first(article, lambda node: node.tag == "img")
        description = _first(article, lambda node: node.tag == "p" and self._has_class_ancestor(node, "card-body"))
        status = next((node.text().strip() for node in article.descendants("a") if "?estados" in node.attrs.get("href", "")), "")
        genres = tuple(node.text().strip() for node in article.descendants("a") if "?generos" in node.attrs.get("href", "") and node.text().strip())
        return SourceSeries(
            source_id=slug,
            title=title.text().strip() if title else slug,
            source_name=self.name,
            cover_url=_image_url(image, str(response.url)) if image else None,
            description=description.text().strip() if description else None,
            status=self._status(status),
            content_tags=genres,
            web_url=str(response.url),
        )

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        slug = self._slug(series.source_id if isinstance(series, SourceSeries) else str(series))
        result: list[SourceChapter] = []
        page = 1
        while True:
            response = await self._request(
                "GET", f"{self.base_url}/series/{slug}/",
                params={"pagina": str(page)}, headers=self._headers(),
            )
            response.raise_for_status()
            root = _parse_html(response.text)
            anchors = [
                node for node in root.descendants("a")
                if node.has_class("card") and self._has_class_ancestor(node, "grid")
                and self._has_class_ancestor(node, "card")
            ]
            for anchor in anchors:
                title = _first(anchor, lambda node: node.has_class("card-title"))
                if title is None:
                    continue
                url = urljoin(str(response.url), anchor.attrs.get("href", ""))
                date = _first(anchor, lambda node: node.tag == "time")
                number = re.search(r"(\d+(?:\.\d+)?)", title.text())
                result.append(SourceChapter(
                    source_id=urlparse(url).path,
                    title=title.text().strip(),
                    series_id=slug,
                    source_name=self.name,
                    number=float(number.group()) if number else None,
                    language=self.language,
                    uploaded_at=self._date(date.attrs.get("datetime", "") if date else ""),
                ))
            if not anchors or not self._has_next(response.text):
                return result
            page += 1

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else str(chapter)
        response = await self._request("GET", urljoin(f"{self.base_url}/", chapter_id))
        response.raise_for_status()
        images = self._page_images(response)
        if not images and "permitir nsfw" in response.text.casefold():
            response = await self._request(
                "GET", urljoin(f"{self.base_url}/", chapter_id),
                headers={"Cookie": "is-adult-enabled=true", "Referer": f"{self.base_url}/"},
            )
            response.raise_for_status()
            images = self._page_images(response)
        return [SourcePage(
            source_id=url,
            chapter_id=chapter_id,
            index=index,
            filename=urlparse(url).path.rsplit("/", 1)[-1] or f"{index}.jpg",
            source_name=self.name,
        ) for index, url in enumerate(images, 1)]

    def _headers(self) -> dict[str, str]:
        headers = {"Referer": f"{self.base_url}/"}
        if getattr(self, "preferences", {}).get("show_nsfw", False):
            headers["Cookie"] = "is-adult-enabled=true"
        return headers

    def _cards(self, response, profile: str) -> list[SourceSeries]:
        root = _parse_html(response.text)
        result: list[SourceSeries] = []
        containers = (
            [node for node in root.descendants("div") if node.has_class("card")]
            if profile == "popular"
            else [node for node in root.descendants("a") if node.has_class("card")]
        )
        for container in containers:
            anchor = (
                _first(container, lambda node: node.tag == "a" and self._has_class_ancestor(node, "card-actions"))
                if profile == "popular"
                else container
            )
            if anchor is None:
                continue
            href = anchor.attrs.get("href", "")
            if "/series/" not in href:
                continue
            title = _first(container, lambda node: node.has_class("card-title") or node.tag == "h3")
            if title is None:
                continue
            image = _first(container, lambda node: node.tag == "img")
            slug = self._slug(href)
            result.append(SourceSeries(
                source_id=slug,
                title=title.text().strip(),
                source_name=self.name,
                cover_url=_image_url(image, str(response.url)) if image else None,
                web_url=urljoin(str(response.url), href),
            ))
        return list({item.source_id: item for item in result}.values())

    def _page_images(self, response) -> list[str]:
        root = _parse_html(response.text)
        return [
            _image_url(image, str(response.url))
            for image in root.descendants("img")
            if self._has_class_ancestor(image, "img")
        ]

    @staticmethod
    def _slug(value: str) -> str:
        parts = [part for part in urlparse(value).path.split("/") if part]
        return parts[-1] if parts else value

    @staticmethod
    def _status(value: str) -> str | None:
        return {"cancelada": "cancelled", "completa": "completed", "en curso": "ongoing", "hiatus": "hiatus"}.get(value.casefold())

    @staticmethod
    def _date(value: str) -> str | None:
        try:
            cleaned = value.partition("(")[0].strip().replace("GMT", "")
            return datetime.strptime(cleaned, "%a %b %d %Y %H:%M:%S %z").isoformat()
        except ValueError:
            return None

    @staticmethod
    def _has_next(html: str) -> bool:
        root = _parse_html(html)
        nav = _first(root, lambda node: node.tag == "nav" and node.attrs.get("aria-label") == "pagination")
        anchors = nav.descendants("a") if nav else []
        return bool(anchors and "btn-disabled" not in anchors[-1].attrs.get("class", ""))


class IkuhentaiSource(GenericSource):
    sort_options: tuple[tuple[str, str], ...] = ()
    status_options: tuple[tuple[str, str], ...] = ()
    genre_options: tuple[tuple[str, str], ...] = ()
    date_locale = "es"

    def __init__(self, fetcher=None) -> None:
        super().__init__(fetcher)
        self.image_headers = {"Referer": f"{self.base_url}/"}

    def get_filters(self) -> list[SourceFilter]:
        return [
            SourceFilter("author", "Autor", "text", default=""),
            SourceFilter("release", "Año de publicación", "text", default=""),
            SourceFilter("sort", "Ordenar por", "select", list(self.sort_options), ""),
            SourceFilter("statuses", "Estado", "multi_select", list(self.status_options), []),
            SourceFilter("genres", "Genres", "multi_select", list(self.genre_options), []),
        ]

    async def browse(self, kind: str, page: int = 1):
        if kind not in {"popular", "latest"}:
            return {"items": [], "has_more": False}
        path = "" if page == 1 else f"page/{page}/"
        response = await self._request(
            "GET", f"{self.base_url}/{path}",
            params={"s": "", "post_type": "wp-manga", "m_orderby": "views" if kind == "popular" else "latest"},
        )
        response.raise_for_status()
        return self._listing(response)

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        values = filters or {}
        path = "" if page == 1 else f"page/{page}/"
        params: list[tuple[str, str]] = [("s", query), ("post_type", "wp-manga")]
        params.extend(("genre[]", str(value)) for value in values.get("genres", []))
        params.extend(("status[]", str(value)) for value in values.get("statuses", []))
        if values.get("sort"):
            params.append(("m_orderby", str(values["sort"])))
        params.extend(
            (key, str(values[key])) for key in ("author", "release") if values.get(key)
        )
        response = await self._request("GET", f"{self.base_url}/{path}", params=params)
        response.raise_for_status()
        return self._listing(response)

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        info = _first(root, lambda node: node.tag == "div" and node.has_class("site-content")) or root
        title = _first(info, lambda node: node.tag in {"h1", "h2"})
        image = _first(info, lambda node: node.tag == "img" and self._has_class_ancestor(node, "summary_image"))
        description = _first(root, lambda node: node.has_class("description-summary"))
        author = _first(info, lambda node: node.has_class("author-content"))
        artist = _first(info, lambda node: node.has_class("artist-content"))
        genres = tuple(
            node.text().strip() for node in info.descendants("a")
            if self._has_class_ancestor(node, "genres-content") and node.text().strip()
        )
        status = ""
        for item in info.descendants("div"):
            if not item.has_class("post-content_item"):
                continue
            heading = _first(item, lambda node: node.tag == "h5" and "estado" in node.text().casefold())
            value = _first(item, lambda node: node.has_class("summary-content"))
            if heading and value:
                status = value.text().strip()
                break
        return SourceSeries(
            source_id=series_id,
            title=(series.title if isinstance(series, SourceSeries) else title.text().strip() if title else series_id.rstrip("/").rsplit("/", 1)[-1]),
            source_name=self.name,
            cover_url=_image_url(image, str(response.url)) if image else None,
            description=description.text().strip() if description else None,
            author=author.text().strip() if author else None,
            artist=artist.text().strip() if artist else None,
            status=self._madara_status(status),
            content_tags=genres,
            web_url=str(response.url),
        )

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        series_url = urljoin(f"{self.base_url}/", series_id).rstrip("/")
        response = await self._request("POST", f"{series_url}/ajax/chapters/")
        response.raise_for_status()
        result: list[SourceChapter] = []
        for item in self._chapter_nodes(_parse_html(response.text)):
            anchor = _first(item, lambda node: node.tag == "a" and bool(node.attrs.get("href")))
            if anchor is None:
                continue
            parsed = urlparse(urljoin(str(response.url), anchor.attrs["href"]))
            query = [(key, value) for key, value in parse_qsl(parsed.query) if key != "style"]
            chapter_url = urlunparse(parsed._replace(query=urlencode([*query, ("style", "list")])))
            title = anchor.text().strip()
            date = _first(item, lambda node: node.tag == "i" and self._has_class_ancestor(node, "chapter-release-date"))
            number = re.search(r"\d+(?:\.\d+)?", title)
            result.append(SourceChapter(
                source_id=chapter_url,
                title=title,
                series_id=series_id,
                source_name=self.name,
                number=float(number.group()) if number else None,
                language=self.language,
                uploaded_at=self._madara_date(date.text() if date else ""),
            ))
        return result

    def _listing(self, response) -> dict:
        root = _parse_html(response.text)
        items: list[SourceSeries] = []
        for item in root.descendants("div"):
            if not (item.has_class("page-item-detail") or item.has_class("c-tabs-item__content")):
                continue
            anchor = _first(
                item,
                lambda node: node.tag == "a" and bool(node.attrs.get("href"))
                and node.parent is not None
                and (node.parent.has_class("item-thumb") or node.parent.has_class("tab-thumb")),
            )
            if anchor is None:
                continue
            url = urljoin(str(response.url), anchor.attrs["href"])
            image = _first(item, lambda node: node.tag == "img")
            items.append(SourceSeries(
                source_id=urlparse(url).path,
                title=anchor.attrs.get("title", "").strip() or anchor.text().strip(),
                source_name=self.name,
                cover_url=_image_url(image, str(response.url)) if image else None,
                web_url=url,
            ))
        has_more = bool(_first(root, lambda node: node.tag == "a" and (node.has_class("nextpostslink") or self._has_class_ancestor(node, "nav-previous"))))
        return {"items": items, "has_more": has_more}


class InMangaSource(GenericSource):
    image_cdn = "https://cdn1.intomanga.com"

    def __init__(self, fetcher=None) -> None:
        super().__init__(fetcher)
        self.image_headers = {"Referer": f"{self.base_url}/"}

    async def browse(self, kind: str, page: int = 1):
        if kind not in {"popular", "latest"}:
            return {"items": [], "has_more": False}
        return await self._catalog("", page, "1" if kind == "popular" else "3")

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        return await self._catalog(query, page, "1")

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        panel = _first(
            root,
            lambda node: node.tag == "div" and node.has_class("panel") and node.has_class("widget")
            and self._has_class_ancestor(node, "col-md-3"),
        )
        info = _first(root, lambda node: node.tag == "div" and node.has_class("col-md-9"))
        image = _first(panel, lambda node: node.tag == "img") if panel else None
        title = _first(info, lambda node: node.tag == "h1") if info else None
        description = _first(info, lambda node: node.tag == "div" and node.has_class("panel-body")) if info else None
        status = ""
        if panel:
            status_link = _first(
                panel,
                lambda node: node.tag == "a" and node.has_class("list-group-item")
                and "estado" in node.text().casefold(),
            )
            status_node = _first(status_link, lambda node: node.tag == "span") if status_link else None
            status = status_node.text().strip() if status_node else ""
        return SourceSeries(
            source_id=series_id,
            title=title.text().strip() if title else series.title if isinstance(series, SourceSeries) else series_id.rstrip("/").rsplit("/", 1)[-1],
            source_name=self.name,
            cover_url=_image_url(image, str(response.url)) if image else None,
            description=description.text().strip() if description else None,
            status=self._madara_status(status),
            web_url=str(response.url),
        )

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        slug = urlparse(series_id).path.rstrip("/").rsplit("/", 1)[-1]
        response = await self._request(
            "GET", f"{self.base_url}/chapter/getall", params={"mangaIdentification": slug},
        )
        response.raise_for_status()
        outer = response.json() if hasattr(response, "json") else json.loads(response.text)
        data = outer.get("data")
        payload = json.loads(data) if data else {}
        if not payload.get("success"):
            return []
        result: list[SourceChapter] = []
        for item in payload.get("result", []):
            number = item.get("Number")
            identification = item.get("Identification")
            if not identification:
                continue
            result.append(SourceChapter(
                source_id=f"/chapter/chapterIndexControls?identification={identification}",
                title=f"Chapter {item.get('FriendlyChapterNumber') or ''}".strip(),
                series_id=series_id,
                source_name=self.name,
                number=float(number) if number is not None else 0.0,
                language=self.language,
                uploaded_at=self._date(str(item.get("RegistrationDate", ""))),
            ))
        return sorted(result, key=lambda chapter: chapter.number or 0, reverse=True)

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else str(chapter)
        response = await self._request("GET", urljoin(f"{self.base_url}/", chapter_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        chapter_input = _first(root, lambda node: node.tag == "input" and node.attrs.get("id") == "ChapterIdentification")
        manga_input = _first(root, lambda node: node.tag == "input" and node.attrs.get("id") == "MangaIdentification")
        chapter_value = chapter_input.attrs.get("value", "") if chapter_input else ""
        manga_value = manga_input.attrs.get("value", "") if manga_input else ""
        return [
            SourcePage(
                source_id=f"{self.image_cdn}/i/m/{manga_value}/c/{chapter_value}/o/{image.attrs['id']}.jpg",
                chapter_id=chapter_id,
                index=index,
                filename=f"{image.attrs['id']}.jpg",
                source_name=self.name,
            )
            for index, image in enumerate(root.descendants("img"), 1)
            if image.has_class("ImageContainer") and image.attrs.get("id")
        ]

    async def _catalog(self, query: str, page: int, sort: str) -> dict:
        body = urlencode({
            "filter[generes][]": "-1",
            "filter[queryString]": query,
            "filter[skip]": str((page - 1) * 10),
            "filter[take]": "10",
            "filter[sortby]": sort,
            "filter[broadcastStatus]": "0",
            "filter[onlyFavorites]": "false",
            "d": "",
        })
        response = await self._request(
            "POST", f"{self.base_url}/manga/getMangasConsultResult",
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest",
            },
            content=body,
        )
        response.raise_for_status()
        root = _parse_html(response.text)
        items: list[SourceSeries] = []
        for anchor in root.descendants("a"):
            # El Kotlin selecciona "body > a": Jsoup envuelve el fragmento en un documento
            # completo, pero aqui los anchors de primer nivel cuelgan de la raiz sin etiqueta.
            if anchor.parent is None or not anchor.attrs.get("href"):
                continue
            if anchor.parent.tag not in {"", "body", "html"}:
                continue
            title = _first(anchor, lambda node: node.tag == "h4" and node.has_class("m0"))
            if title is None:
                continue
            url = urljoin(str(response.url), anchor.attrs["href"])
            parsed = urlparse(url)
            image = _first(anchor, lambda node: node.tag == "img")
            items.append(SourceSeries(
                source_id=urlunparse(("", "", parsed.path, parsed.params, parsed.query, "")),
                title=title.text().strip(),
                source_name=self.name,
                cover_url=_image_url(image, str(response.url)) if image else None,
                web_url=url,
            ))
        return {"items": items, "has_more": len(items) == 10}

    @staticmethod
    def _date(value: str) -> str | None:
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").isoformat()
        except ValueError:
            return None


class InsanosScanSource(GenericSource):
    date_format = "dd MMM yyyy"
    date_locale = "es"

    def __init__(self, fetcher=None) -> None:
        super().__init__(fetcher)
        self._nonce: str | None = None
        self.image_headers = {"Referer": f"{self.base_url}/"}

    def get_preferences(self) -> list[SourcePreference]:
        return [SourcePreference(
            "show_paid_chapters", "Mostrar capítulos de pago", "checkbox", default=False,
        )]

    async def browse(self, kind: str, page: int = 1):
        if kind not in {"popular", "latest"}:
            return {"items": [], "has_more": False}
        response = await self._request(
            "GET", f"{self.base_url}/manga/",
            params={"orderby": "views" if kind == "popular" else "date", "page": str(page)},
        )
        response.raise_for_status()
        return self._catalog(response)

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        response = await self._request(
            "POST", f"{self.base_url}/wp-admin/admin-ajax.php",
            data={"action": "adar_search", "nonce": await self._search_nonce(), "query": query},
        )
        response.raise_for_status()
        payload = response.json() if hasattr(response, "json") else json.loads(response.text)
        items: list[SourceSeries] = []
        for item in payload.get("data") or []:
            url = urljoin(f"{self.base_url}/", str(item.get("url", "")))
            parsed = urlparse(url)
            items.append(SourceSeries(
                source_id=urlunparse(("", "", parsed.path, parsed.params, parsed.query, "")),
                title=str(item.get("title", "")),
                source_name=self.name,
                cover_url=str(item.get("cover") or "") or None,
                web_url=url,
            ))
        return {"items": items, "has_more": False}

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        title = _first(root, lambda node: node.tag == "h1" and node.has_class("series-main-title"))
        image = _first(root, lambda node: node.tag == "img" and node.has_class("series-cover-img"))
        description = _first(root, lambda node: node.tag == "div" and node.has_class("synopsis-content"))
        status = _first(root, lambda node: node.tag == "span" and node.has_class("data-badge--status"))
        genres = tuple(
            node.text().strip() for node in root.descendants("a")
            if node.has_class("genre-pill") and self._has_class_ancestor(node, "genres-cell") and node.text().strip()
        )
        return SourceSeries(
            source_id=series_id,
            title=title.text().strip() if title else series.title if isinstance(series, SourceSeries) else series_id.rstrip("/").rsplit("/", 1)[-1],
            source_name=self.name,
            cover_url=_image_url(image, str(response.url)) if image else None,
            description=description.text().strip() if description else None,
            status=self._madara_status(status.text() if status else ""),
            content_tags=genres,
            web_url=str(response.url),
        )

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        locked = self._locked_paths(root)
        show_paid = bool(getattr(self, "preferences", {}).get("show_paid_chapters", False))
        result: list[SourceChapter] = []
        for anchor in root.descendants("a"):
            if not anchor.has_class("chapter-row") or not anchor.attrs.get("href"):
                continue
            url = urljoin(str(response.url), anchor.attrs["href"])
            parsed = urlparse(url)
            path = parsed.path.rstrip("/") + "/"
            if not show_paid and path in locked:
                continue
            number_node = _first(anchor, lambda node: node.tag == "span" and node.has_class("chapter-row__num"))
            title_node = _first(anchor, lambda node: node.tag == "span" and node.has_class("chapter-row__title"))
            date = _first(anchor, lambda node: node.tag == "span" and node.has_class("chapter-row__date"))
            title = (number_node or title_node).text().strip() if number_node or title_node else "Capítulo"
            number = re.search(r"\d+(?:\.\d+)?", title)
            result.append(SourceChapter(
                source_id=urlunparse(("", "", parsed.path, parsed.params, parsed.query, "")),
                title=f"{title} 🔒" if path in locked else title,
                series_id=series_id,
                source_name=self.name,
                number=float(number.group()) if number else None,
                language=self.language,
                uploaded_at=self._madara_date(date.text() if date else ""),
            ))
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else str(chapter)
        response = await self._request("GET", urljoin(f"{self.base_url}/", chapter_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        reader = _first(root, lambda node: node.tag == "div" and node.has_class("reader-pages"))
        images: list[_Node] = []
        if reader is not None and reader.parent is not None:
            after_reader = False
            for sibling in reader.parent.children:
                if sibling is reader:
                    after_reader = True
                elif after_reader and isinstance(sibling, _Node) and sibling.tag == "div":
                    images.extend(sibling.descendants("img"))
        if not images:
            body = _first(root, lambda node: node.tag == "body" and node.has_class("reader-body"))
            images = [
                image for image in body.descendants("img") if "adar_manga" in image.attrs.get("src", "")
            ] if body else []
        urls = [
            urljoin(str(response.url), image.attrs.get("src") or image.attrs.get("data-src", ""))
            for image in images if image.attrs.get("src") or image.attrs.get("data-src")
        ]
        return [SourcePage(
            source_id=url,
            chapter_id=chapter_id,
            index=index,
            filename=urlparse(url).path.rsplit("/", 1)[-1] or f"{index}.jpg",
            source_name=self.name,
        ) for index, url in enumerate(urls, 1)]

    async def _search_nonce(self) -> str:
        if self._nonce is not None:
            return self._nonce
        response = await self._request("GET", self.base_url)
        response.raise_for_status()
        script = _first(
            _parse_html(response.text),
            lambda node: node.tag == "script" and node.attrs.get("id") == "adar-main-js-extra",
        )
        encoded = script.attrs.get("src", "").removeprefix("data:text/javascript;base64,") if script else ""
        try:
            javascript = base64.b64decode(encoded).decode()
        except (ValueError, UnicodeDecodeError):
            javascript = ""
        found = re.search(r'''["']nonce["']\s*:\s*["']([^"']+)["']''', javascript)
        self._nonce = found.group(1) if found else ""
        return self._nonce

    def _catalog(self, response) -> dict:
        root = _parse_html(response.text)
        items: list[SourceSeries] = []
        for article in root.descendants("article"):
            if not article.has_class("catalog-card"):
                continue
            anchor = _first(article, lambda node: node.tag == "a" and node.has_class("catalog-card__link"))
            title = _first(article, lambda node: node.tag == "h2" and node.has_class("catalog-card__title"))
            if anchor is None or title is None:
                continue
            url = urljoin(str(response.url), anchor.attrs.get("href", ""))
            parsed = urlparse(url)
            image = _first(article, lambda node: node.tag == "img" and node.has_class("catalog-card__cover"))
            items.append(SourceSeries(
                source_id=urlunparse(("", "", parsed.path, parsed.params, parsed.query, "")),
                title=title.text().strip(),
                source_name=self.name,
                cover_url=_image_url(image, str(response.url)) if image else None,
                web_url=url,
            ))
        has_more = bool(_first(
            root,
            lambda node: node.tag == "a" and node.has_class("next") and node.has_class("page-numbers")
            and self._has_class_ancestor(node, "catalog-pagination"),
        ))
        return {"items": items, "has_more": has_more}

    @staticmethod
    def _locked_paths(root: _Node) -> set[str]:
        script = next(
            (node.text() for node in root.descendants("script") if not node.attrs.get("src") and "var locked" in node.text()),
            "",
        )
        match = re.search(r"var locked\s*=\s*(\{[^;]+\});", script)
        if not match:
            return set()
        try:
            return {path for path, value in json.loads(match.group(1)).items() if int(value) > 0}
        except (ValueError, TypeError, json.JSONDecodeError):
            return set()


class JeazScansSource(GenericSource):
    date_format = "dd MMM, yyyy"
    date_locale = "en"

    async def browse(self, kind: str, page: int = 1):
        if kind not in {"popular", "latest"}:
            return {"items": [], "has_more": False}
        response = await self._request("GET", f"{self.base_url}/")
        response.raise_for_status()
        return {"items": self._home(response, kind), "has_more": False}

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        if not query.strip():
            return await self.browse("latest", page)
        response = await self._request(
            "GET", f"{self.base_url}/ajax_search.php", params={"q": query.strip()},
        )
        response.raise_for_status()
        payload = response.json() if hasattr(response, "json") else json.loads(response.text)
        items: list[SourceSeries] = []
        for item in payload:
            if item.get("id") == -1 or not str(item.get("titulo", "")).strip():
                continue
            source_id = f"/manga.php?id={item['id']}"
            cover = str(item.get("portada") or "").strip()
            items.append(SourceSeries(
                source_id=source_id,
                title=str(item["titulo"]).strip(),
                source_name=self.name,
                cover_url=urljoin(f"{self.base_url}/", cover) if cover else None,
                web_url=urljoin(f"{self.base_url}/", source_id),
            ))
        return {"items": items, "has_more": False}

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        title = _first(root, lambda node: node.tag == "h1" and node.has_class("blood-title"))
        image = _first(
            root,
            lambda node: node.tag == "img" and self._has_class_ancestor(node, "cultivation-panel")
            and self._has_class_ancestor(node, "lg:col-span-3"),
        )
        description = next(
            (
                node for node in root.descendants("div") if node.has_class("text-gray-200")
                and any(child.tag == "h3" and "sinopsis" in child.text().casefold() for child in node.descendants("h3"))
            ),
            _first(root, lambda node: node.tag == "div" and node.has_class("text-gray-200")),
        )
        status = _first(root, lambda node: node.tag == "span" and node.has_class("status-badge"))
        genres = tuple(
            node.text().strip() for node in root.descendants("a")
            if "directorio.php?genero=" in node.attrs.get("href", "") and node.text().strip()
        )
        synopsis = re.sub(r"^SINOPSIS:?\s*", "", description.text().strip(), flags=re.I) if description else ""
        return SourceSeries(
            source_id=series_id,
            title=title.text().strip() if title else series.title if isinstance(series, SourceSeries) else series_id,
            source_name=self.name,
            cover_url=_image_url(image, str(response.url)) if image else None,
            description=synopsis or None,
            status=self._status(status.text() if status else ""),
            content_tags=genres,
            web_url=str(response.url),
        )

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        result: list[SourceChapter] = []
        for anchor in root.descendants("a"):
            if not anchor.has_class("chapter-item") or not self._has_id_ancestor(anchor, "chaptersContainer"):
                continue
            url = urljoin(str(response.url), anchor.attrs.get("href", ""))
            parsed = urlparse(url)
            raw_number = anchor.attrs.get("data-chapter-number", "")
            found = re.search(r"capitulo-([0-9.]+)", url, re.I)
            try:
                number = float(raw_number or found.group(1) if found else raw_number)
            except ValueError:
                number = -1.0
            title = _first(anchor, lambda node: node.has_class("chapter-title"))
            date = next(
                (
                    node for node in anchor.descendants("span")
                    if _first(node, lambda child: child.tag == "i" and child.has_class("ph-clock")) is not None
                ),
                None,
            )
            name = title.text().strip() if title and title.text().strip() else f"Chapter {number:g}"
            result.append(SourceChapter(
                source_id=urlunparse(("", "", parsed.path, parsed.params, parsed.query, "")),
                title=name,
                series_id=series_id,
                source_name=self.name,
                number=number,
                language=self.language,
                uploaded_at=self._madara_date(date.text() if date else ""),
            ))
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else str(chapter)
        response = await self._request("GET", urljoin(f"{self.base_url}/", chapter_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        # El lector servia img.protected-img con data-verify; ahora sirve un <img src> normal
        # dentro de div.page-container, asi que la clase ya no puede ser obligatoria.
        images = [
            image for image in root.descendants("img")
            if self._has_class_ancestor(image, "page-container")
            or self._has_class_ancestor(image, "reader-body")
            or self._has_class_ancestor(image, "reading-content")
        ]
        # Cada page-container repite su imagen dentro de un <noscript>: misma URL, sobra.
        urls = list(dict.fromkeys(
            url for image in images if (url := self._page_url(image, str(response.url)))
        ))
        if not urls:
            slug, chapter_number = self._slug_and_chapter(response)
            if not slug or not chapter_number:
                raise ValueError("No se pudo identificar el capítulo para la API de Jeaz Scans")
            parsed = urlparse(str(response.url))
            api_url = urlunparse(parsed._replace(
                path="/api_lector.php", query=urlencode({"slug": slug, "cap": chapter_number}), fragment="",
            ))
            api = await self._request("GET", api_url, headers={"Referer": str(response.url)})
            api.raise_for_status()
            payload = api.json() if hasattr(api, "json") else json.loads(api.text)
            if not payload.get("success"):
                raise ValueError("La API de Jeaz Scans devolvió un error")
            urls = list(dict.fromkeys(
                url for item in sorted(payload.get("paginas", []), key=lambda value: value.get("orden", 0))
                if (url := self._decode_verify(str(item.get("data_verify", ""))))
            ))
        return [SourcePage(
            source_id=url,
            chapter_id=chapter_id,
            index=index,
            filename=urlparse(url).path.rsplit("/", 1)[-1] or f"{index}.jpg",
            source_name=self.name,
        ) for index, url in enumerate(urls, 1)]

    # La home sirve "Popular hoy" (a.popular-card) y "Ultimos lanzamientos"
    # (article.manga-card). "Top Rankings" sigue ahi pero la rellena JS: llega vacia.
    _HOME_MARKERS = {
        "popular": ("popular hoy", "top rankings"),
        "latest": ("lanzamientos",),
    }
    _HOME_CARD_CLASSES = {"popular": ("popular-card",), "latest": ("manga-card",)}

    def _home(self, response, kind: str) -> list[SourceSeries]:
        root = _parse_html(response.text)
        markers = self._HOME_MARKERS[kind]
        classes = self._HOME_CARD_CLASSES[kind]
        sections = [
            node for node in root.descendants("section")
            if any(
                marker in heading.text().casefold()
                for marker in markers
                for tag in ("h2", "h3")
                for heading in node.descendants(tag)
            )
        ]
        result: list[SourceSeries] = []
        seen: set[str] = set()
        for section in sections:
            containers = [
                node for node in section.descendants()
                if any(node.has_class(value) for value in classes)
            ] or [
                node for node in section.descendants("a")
                if "manga.php?id=" in node.attrs.get("href", "")
            ]
            for container in containers:
                anchor = container if (
                    container.tag == "a" and "manga.php?id=" in container.attrs.get("href", "")
                ) else _first(
                    container,
                    lambda node: node.tag == "a" and "manga.php?id=" in node.attrs.get("href", ""),
                )
                if anchor is None:
                    continue
                image = _first(container, lambda node: node.tag == "img")
                title = self._home_title(container, image)
                if not title:
                    continue
                url = urljoin(str(response.url), anchor.attrs["href"])
                parsed = urlparse(url)
                source_id = urlunparse(("", "", parsed.path, parsed.params, parsed.query, ""))
                if source_id in seen:
                    continue
                seen.add(source_id)
                result.append(SourceSeries(
                    source_id=source_id,
                    title=title,
                    source_name=self.name,
                    cover_url=_image_url(image, str(response.url)) if image else None,
                    web_url=url,
                ))
        return result

    @staticmethod
    def _home_title(container: _Node, image: _Node | None) -> str:
        # release-title y popular-info>strong son el markup actual; h4/h5/figcaption, el anterior.
        node = _first(container, lambda item: item.tag == "a" and item.has_class("release-title"))
        node = node or _first(container, lambda item: item.tag == "strong")
        node = node or _first(container, lambda item: item.tag in {"h4", "h5", "figcaption"})
        if node is not None and node.text().strip():
            return node.text().strip()
        return (image.attrs.get("alt", "").strip() if image else "") or ""

    def _page_url(self, image: _Node, base_url: str) -> str | None:
        if image.attrs.get("data-verify"):
            return self._decode_verify(image.attrs["data-verify"])
        raw = image.attrs.get("data-sec-src") or image.attrs.get("data-src") or image.attrs.get("src")
        return urljoin(base_url, raw) if raw else None

    @staticmethod
    def _decode_verify(value: str) -> str | None:
        try:
            url = base64.b64decode(value).decode()[::-1].strip()
        except (ValueError, UnicodeDecodeError):
            return None
        return url if url.startswith("http") else None

    @staticmethod
    def _slug_and_chapter(response) -> tuple[str, str]:
        parsed = urlparse(str(response.url))
        query = dict(parse_qsl(parsed.query))
        if query.get("manga") and query.get("cap"):
            return query["manga"].strip(), query["cap"].strip()
        found = re.search(r"/leer/([^/]+)/capitulo-([0-9.]+)", parsed.path, re.I)
        if found:
            return found.group(1), found.group(2)
        scripts = "\n".join(node.text() for node in _parse_html(response.text).descendants("script"))
        slug = re.search(r'''MANGA_SLUG\s*=\s*["']([^"']+)["']''', scripts)
        chapter = re.search(r'''CAP_INICIAL\s*=\s*["']([^"']+)["']''', scripts)
        return (slug.group(1), chapter.group(1)) if slug and chapter else ("", "")

    @staticmethod
    def _status(value: str) -> str | None:
        normalized = value.casefold()
        if "complet" in normalized:
            return "completed"
        if any(marker in normalized for marker in ("pausa", "hiato")):
            return "hiatus"
        if any(marker in normalized for marker in ("cancel", "aband")):
            return "cancelled"
        if any(marker in normalized for marker in ("cultivo", "curso", "ongoing", "emision", "emisión")):
            return "ongoing"
        return None


class KoinoboriScanSource(GenericSource):
    api_base_url = "https://api.visorkoi.com"

    def __init__(self, fetcher=None) -> None:
        super().__init__(fetcher)
        self._series_list: list[dict] = []
        self.image_headers = {"Referer": f"{self.base_url}/"}

    async def browse(self, kind: str, page: int = 1):
        if kind not in {"popular", "latest"}:
            return {"items": [], "has_more": False}
        endpoint = "topSeries" if kind == "popular" else "lastupdates"
        response = await self._request("GET", f"{self.api_base_url}/api/{endpoint}")
        response.raise_for_status()
        payload = response.json() if hasattr(response, "json") else json.loads(response.text)
        values = (
            [*payload.get("mensualRes", []), *payload.get("weekRes", []), *payload.get("dayRes", [])]
            if kind == "popular" else payload
        )
        unique = {str(item.get("series_slug", "")): item for item in reversed(values)}
        return {"items": [self._manga(item) for item in reversed(unique.values())], "has_more": False}

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        if not self._series_list:
            response = await self._request("GET", f"{self.api_base_url}/api/allComics")
            response.raise_for_status()
            self._series_list = list(response.json() if hasattr(response, "json") else json.loads(response.text))
        needle = query.casefold()
        matches = [item for item in self._series_list if needle in str(item.get("title", "")).casefold()]
        start = (page - 1) * 24
        return {
            "items": [self._manga(item) for item in matches[start:start + 24]],
            "has_more": len(matches) > start + 24,
        }

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        slug = self._slug(series_id)
        response = await self._request("GET", f"{self.base_url}/comic/{slug}")
        response.raise_for_status()
        item = self._payload(response)
        tags = tuple(str(tag.get("name", "")).strip() for tag in item.get("tags") or [] if str(tag.get("name", "")).strip())
        return SourceSeries(
            source_id=slug,
            title=str(item.get("title") or slug).strip(),
            source_name=self.name,
            cover_url=str(item.get("thumbnail") or "") or None,
            description=str(item.get("description") or "").strip() or None,
            author=str(item.get("author") or "").strip() or None,
            status={"Ongoing": "ongoing", "Completado": "completed", "Abandonado": "cancelled", "Pausado": "hiatus"}.get(str(item.get("status", "")).strip()),
            content_tags=tags,
            web_url=str(response.url),
        )

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        slug = self._slug(series_id)
        response = await self._request("GET", f"{self.base_url}/comic/{slug}")
        response.raise_for_status()
        payload = self._payload(response)
        series_slug = str(payload.get("series_slug") or slug)
        result: list[SourceChapter] = []
        for season in payload.get("Season", []):
            for item in season.get("Chapter", []):
                name = str(item.get("chapter_name") or "Chapter").strip()
                title = str(item.get("chapter_title") or "").strip()
                number = re.search(r"\d+(?:\.\d+)?", name)
                result.append(SourceChapter(
                    source_id=f"/comic/{series_slug}/{item.get('chapter_slug', '')}",
                    title=f"{name}: {title}" if title else name,
                    series_id=series_id,
                    source_name=self.name,
                    number=float(number.group()) if number else None,
                    language=self.language,
                    uploaded_at=self._date(str(item.get("created_at", ""))),
                ))
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else str(chapter)
        response = await self._request("GET", urljoin(f"{self.base_url}/", chapter_id))
        response.raise_for_status()
        urls = [
            urljoin(str(response.url), image.attrs["src"])
            for image in _parse_html(response.text).descendants("img")
            if image.attrs.get("src") and image.parent is not None
            and image.parent.tag == "div" and image.parent.has_class("relative")
        ]
        return [SourcePage(
            source_id=url,
            chapter_id=chapter_id,
            index=index,
            filename=urlparse(url).path.rsplit("/", 1)[-1] or f"{index}.jpg",
            source_name=self.name,
        ) for index, url in enumerate(urls, 1)]

    def _manga(self, item: dict) -> SourceSeries:
        slug = str(item.get("series_slug", ""))
        return SourceSeries(
            source_id=slug,
            title=str(item.get("title", "")),
            source_name=self.name,
            cover_url=str(item.get("thumbnail") or "") or None,
            web_url=f"{self.base_url}/comic/{slug}",
        )

    @staticmethod
    def _slug(value: str) -> str:
        parts = [part for part in urlparse(value).path.split("/") if part]
        return parts[-1] if parts else value

    @staticmethod
    def _payload(response) -> dict:
        scripts = "\n".join(node.text() for node in _parse_html(response.text).descendants("script"))
        found = re.search(r'''self\.__next_f\.push\(.*?info\\":(\{.*Chapter.*\}).*?\\"userIsFollowed''', scripts, re.S)
        if not found:
            raise ValueError("No se pudo obtener la información de Koinobori Scan")
        return json.loads(re.sub(r"\\(.)", r"\1", found.group(1)))

    @staticmethod
    def _date(value: str) -> str | None:
        try:
            return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            return None


class LeerCapituloSource(GenericSource):
    genre_options: tuple[tuple[str, str], ...] = ()
    alphabet_options: tuple[tuple[str, str], ...] = ()
    status_options: tuple[tuple[str, str], ...] = ()
    # ponytail: current site alphabet; add a JS deobfuscator only if rotations become frequent.
    decoder_keys = (
        "EzCIUe3plcrfxuv9hKOsVtkTA6ZjaXRQJ0wWqb5D8gm1nG7LoH2dFyNYB4PiMS",
        "xXHbvV7snRpMFkrUPqlS4BzG3jg1aYC5WJ0wcZiLtoAyedQ8D2fTNOI9Eu6mhK",
    )

    def get_filters(self) -> list[SourceFilter]:
        return [
            SourceFilter("genre", "Género", "select", list(self.genre_options), ""),
            SourceFilter("alphabet", "Alfabético", "select", list(self.alphabet_options), ""),
            SourceFilter("status", "Estado", "select", list(self.status_options), ""),
        ]

    async def browse(self, kind: str, page: int = 1):
        if kind not in {"popular", "latest"}:
            return {"items": [], "has_more": False}
        response = await self._request("GET", self.base_url)
        response.raise_for_status()
        return {
            "items": self._popular(response) if kind == "popular" else self._latest(response),
            "has_more": False,
        }

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        if query.strip():
            response = await self._request(
                "GET", f"{self.base_url}/search-autocomplete", params={"term": query.strip()},
            )
            response.raise_for_status()
            payload = response.json() if hasattr(response, "json") else json.loads(response.text)
            return {"items": [self._autocomplete(item) for item in payload], "has_more": False}
        values = filters or {}
        selected = next(
            ((kind, str(values.get(kind, ""))) for kind in ("genre", "alphabet", "status") if values.get(kind)),
            None,
        )
        if selected is None:
            raise ValueError("Debe seleccionar un filtro o realizar una búsqueda por texto")
        kind, value = selected
        path = "initial" if kind == "alphabet" else kind
        response = await self._request(
            "GET", f"{self.base_url}/{path}/{quote(value, safe='')}/", params={"page": str(page)},
        )
        response.raise_for_status()
        root = _parse_html(response.text)
        return {"items": self._catalog(root, str(response.url)), "has_more": self._has_next(root)}

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        title = _first(root, lambda node: node.tag == "h1")
        description = _first(root, lambda node: node.attrs.get("id") == "example2")
        cover = _first(root, lambda node: node.tag == "img" and self._has_class_ancestor(node, "cover-detail"))
        alt_label = _first(root, lambda node: node.tag == "span" and "títulos alternativos:" in node.text().casefold())
        status_label = _first(root, lambda node: node.tag == "span" and "estado:" in node.text().casefold())
        text = description.text().strip() if description else ""
        if alt_name := self._following_text(alt_label):
            text = f"{text}\n\nAlt name(s): {alt_name}".strip()
        genres = tuple(
            node.text().strip() for node in root.descendants("a")
            if node.attrs.get("href", "").startswith("/genre/") and node.text().strip()
        )
        return SourceSeries(
            source_id=series_id,
            title=title.text().strip() if title else series_id,
            source_name=self.name,
            cover_url=_image_url(cover, str(response.url)) if cover else None,
            description=text or None,
            status={
                "ongoing": "ongoing", "paused": "hiatus", "completed": "completed",
                "cancelled": "cancelled",
            }.get(self._following_text(status_label).casefold()),
            content_tags=genres,
            web_url=str(response.url),
        )

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        result: list[SourceChapter] = []
        for item in root.descendants("li"):
            if not self._has_class_ancestor(item, "chapter-list"):
                continue
            anchor = _first(item, lambda node: node.tag == "a" and node.has_class("xanh"))
            if anchor is None or not anchor.attrs.get("href"):
                continue
            title = anchor.text().strip()
            number = re.search(r"\d+(?:\.\d+)?", title)
            result.append(SourceChapter(
                source_id=urljoin(str(response.url), anchor.attrs["href"]),
                title=title,
                series_id=series_id,
                source_name=self.name,
                number=float(number.group()) if number else None,
                language=self.language,
            ))
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else str(chapter)
        response = await self._request("GET", urljoin(f"{self.base_url}/", chapter_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        encoded = _first(root, lambda node: node.attrs.get("id") == "array_data")
        if encoded is None:
            raise ValueError("No se encontró array_data")
        urls = self._decode_urls(encoded.text().strip(), self.decoder_keys)
        meta = _first(root, lambda node: node.tag == "meta" and node.attrs.get("property") == "ad:check")
        if meta is not None:
            order = [value for value in re.sub(r"\D+", "-", meta.attrs.get("content", "")).split("-") if value]
            reverse_digits = "01" in order
            urls = [urls[int(value[::-1] if reverse_digits else value)] for value in order][::-1]
        return [SourcePage(
            source_id=url,
            chapter_id=chapter_id,
            index=index,
            filename=urlparse(url).path.rsplit("/", 1)[-1] or f"{index}.jpg",
            source_name=self.name,
        ) for index, url in enumerate(urls)]

    def _popular(self, response) -> list[SourceSeries]:
        root = _parse_html(response.text)
        return [
            self._anchor_manga(anchor, str(response.url))
            for anchor in root.descendants("a")
            if self._has_class_ancestor(anchor, "thumbnails") and self._has_class_ancestor(anchor, "hot-manga")
            and anchor.attrs.get("href") and anchor.attrs.get("title")
        ]

    def _latest(self, response) -> list[SourceSeries]:
        return self._catalog(_parse_html(response.text), str(response.url))

    def _catalog(self, root: _Node, base_url: str) -> list[SourceSeries]:
        result: list[SourceSeries] = []
        for item in root.descendants():
            if not item.has_class("mainpage-manga"):
                continue
            body = _first(item, lambda node: node.has_class("media-body"))
            anchor = _first(body or item, lambda node: node.tag == "a" and bool(node.attrs.get("href")))
            if anchor is not None:
                result.append(self._anchor_manga(anchor, base_url, item))
        return result

    def _anchor_manga(self, anchor: _Node, base_url: str, container: _Node | None = None) -> SourceSeries:
        image = _first(container or anchor, lambda node: node.tag == "img")
        heading = _first(container or anchor, lambda node: node.tag == "h4")
        source_id = urljoin(base_url, anchor.attrs.get("href", ""))
        return SourceSeries(
            source_id=source_id,
            title=(heading.text() if heading else anchor.attrs.get("title") or anchor.text()).strip(),
            source_name=self.name,
            cover_url=_image_url(image, base_url) if image else None,
            web_url=source_id,
        )

    def _autocomplete(self, item: dict) -> SourceSeries:
        source_id = urljoin(f"{self.base_url}/", str(item.get("link", "")))
        return SourceSeries(
            source_id=source_id,
            title=str(item.get("label", "")),
            source_name=self.name,
            cover_url=urljoin(f"{self.base_url}/", str(item.get("thumbnail", ""))),
            web_url=source_id,
        )

    @staticmethod
    def _following_text(node: _Node | None) -> str:
        if node is None or node.parent is None:
            return ""
        siblings = node.parent.children
        for sibling in siblings[siblings.index(node) + 1:]:
            value = sibling.strip() if isinstance(sibling, str) else sibling.text().strip()
            if value:
                return value
        return ""

    @staticmethod
    def _has_next(root: _Node) -> bool:
        for listing in root.descendants("ul"):
            if not listing.has_class("pagination"):
                continue
            items = [child for child in listing.children if isinstance(child, _Node) and child.tag == "li"]
            return any(item.has_class("active") and index + 1 < len(items) for index, item in enumerate(items))
        return False

    @staticmethod
    def _decode_urls(value: str, keys: tuple[str, str]) -> list[str]:
        source, encoded = keys
        translated = "".join(source[encoded.index(char)] if char in encoded else char for char in value)
        return base64.b64decode(translated).decode("utf-8").split(",")


class LeerMangaEspSource(GenericSource):
    type_options: tuple[tuple[str, str], ...] = ()
    genre_options: tuple[tuple[str, str], ...] = ()

    @property
    def image_base_url(self) -> str:
        return self.base_url.replace("https://", "https://images.") + "/file/leermangaesp/"

    def get_filters(self) -> list[SourceFilter]:
        return [
            SourceFilter("type", "Tipo", "select", list(self.type_options), ""),
            SourceFilter("genres", "Géneros", "multi_select", list(self.genre_options), []),
        ]

    async def browse(self, kind: str, page: int = 1):
        if kind == "popular":
            response = await self._request("GET", self.base_url)
            response.raise_for_status()
            root = _parse_html(response.text)
            script = _first(root, lambda node: node.tag == "script" and node.attrs.get("id") == "ssr-trends-data")
            payload = json.loads(script.text()) if script else []
        elif kind == "latest":
            response = await self._request("GET", f"{self.base_url}/api/latest_chapters_with_dates")
            response.raise_for_status()
            payload = sorted(
                self._json(response), key=lambda item: str(item.get("fecha_publicacion") or ""), reverse=True,
            )
        else:
            return {"items": [], "has_more": False}
        return {"items": [manga for item in payload if (manga := self._manga(item))], "has_more": False}

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        if slug := self._deeplink_slug(query.strip()):
            response = await self._request("GET", f"{self.base_url}/info/{quote(slug, safe='')}/")
            response.raise_for_status()
            return {"items": [self._details(response, slug)], "has_more": False}
        values = filters or {}
        params = {"page": str(page), "page_size": "20"}
        if query.strip():
            params["query"] = query.strip()
        if values.get("type"):
            params["tipo"] = str(values["type"])
        if values.get("genres"):
            params["generos"] = ",".join(str(value) for value in values["genres"])
        response = await self._request("GET", f"{self.base_url}/api/buscar_mangas", params=params)
        response.raise_for_status()
        payload = self._json(response)
        return {
            "items": [manga for item in payload.get("resultados", []) if (manga := self._manga(item))],
            "has_more": int(payload.get("page", 1)) < int(payload.get("total_pages", 1)),
        }

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        slug = self._slug(series_id)
        response = await self._request("GET", f"{self.base_url}/info/{quote(slug, safe='')}/")
        response.raise_for_status()
        return self._details(response, slug)

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        slug = self._slug(series_id)
        url = f"{self.base_url}/info/{quote(slug, safe='')}/"
        seen: set[str] = set()
        result: list[SourceChapter] = []
        while url:
            response = await self._request("GET", url)
            response.raise_for_status()
            root = _parse_html(response.text)
            for anchor in root.descendants("a"):
                number_text = anchor.attrs.get("data-chapter", "").strip()
                if not anchor.has_class("chapter-link") or anchor.attrs.get("id") == "continue-link" or not number_text:
                    continue
                path = urlparse(urljoin(str(response.url), anchor.attrs.get("href", ""))).path
                if not path or path in seen:
                    continue
                seen.add(path)
                title = _first(anchor, lambda node: node.has_class("chapter-title"))
                date = _first(anchor, lambda node: node.has_class("chapter-date"))
                try:
                    number = float(number_text)
                except ValueError:
                    number = None
                result.append(SourceChapter(
                    source_id=path,
                    title=(title.text() if title else anchor.text()).strip(),
                    series_id=slug,
                    source_name=self.name,
                    number=number,
                    language=self.language,
                    uploaded_at=self._chapter_date(date.text() if date else ""),
                ))
            more = _first(root, lambda node: node.attrs.get("id") == "more-link" and bool(node.attrs.get("href")))
            url = urljoin(str(response.url), more.attrs["href"]) if more else ""
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else str(chapter)
        response = await self._request("GET", urljoin(f"{self.base_url}/", chapter_id))
        response.raise_for_status()
        urls = [
            _image_url(image, str(response.url))
            for image in _parse_html(response.text).descendants("img")
            if image.has_class("manga-image") and self._has_id_ancestor(image, "cascade-view")
        ]
        return [SourcePage(
            source_id=url,
            chapter_id=chapter_id,
            index=index,
            filename=urlparse(url).path.rsplit("/", 1)[-1] or f"{index}.jpg",
            source_name=self.name,
        ) for index, url in enumerate(urls)]

    def _manga(self, item: dict) -> SourceSeries | None:
        title = str(item.get("titulo") or "").strip()
        if not title:
            return None
        slug = str(item.get("slug") or "").strip()
        cover = str(item.get("portada") or "").lstrip("/")
        return SourceSeries(
            source_id=slug,
            title=title,
            source_name=self.name,
            cover_url=urljoin(self.image_base_url, cover) if cover else None,
            web_url=f"{self.base_url}/info/{slug}/",
        )

    def _details(self, response, slug: str) -> SourceSeries:
        root = _parse_html(response.text)
        title = _first(root, lambda node: node.has_class("manga-title")) or _first(root, lambda node: node.tag == "h1")
        if title is None or not title.text().strip():
            raise ValueError("No se pudo obtener el título")
        cover = _first(root, lambda node: node.tag == "img" and node.has_class("manga-cover"))
        description = _first(root, lambda node: node.attrs.get("id") == "synopsis-text")
        status = _first(root, lambda node: node.has_class("info-value") and self._has_id_ancestor(node, "info-block"))
        genres = tuple(
            node.text().strip() for node in root.descendants()
            if node.has_class("genero-item") and self._has_class_ancestor(node, "info-generos") and node.text().strip()
        )
        normalized_status = status.text().casefold() if status else ""
        return SourceSeries(
            source_id=slug,
            title=title.text().strip(),
            source_name=self.name,
            cover_url=_image_url(cover, str(response.url)) if cover else None,
            description=description.text().strip() if description else None,
            status=(
                "ongoing" if "en curso" in normalized_status
                else "completed" if "finalizado" in normalized_status or "completo" in normalized_status
                else None
            ),
            content_tags=genres,
            web_url=str(response.url),
        )

    @staticmethod
    def _json(response):
        return response.json() if hasattr(response, "json") else json.loads(response.text)

    @staticmethod
    def _chapter_date(value: str) -> str | None:
        try:
            return datetime.strptime(value.strip(), "%B %d, %Y").isoformat()
        except ValueError:
            return None

    @staticmethod
    def _slug(value: str) -> str:
        parts = [part for part in urlparse(value).path.split("/") if part]
        return parts[1] if len(parts) > 1 and parts[0] in {"info", "manga", "leer-m"} else parts[-1] if parts else value.strip("/")

    @staticmethod
    def _deeplink_slug(value: str) -> str | None:
        if not value:
            return None
        parsed = urlparse(value if "://" in value else f"https://{value}")
        parts = [part for part in parsed.path.split("/") if part]
        return parts[1] if parsed.hostname and "mangalect" in parsed.hostname and len(parts) > 1 and parts[0].casefold() in {"info", "manga", "leer-m"} else None


class LectorJpgSource(GenericSource):
    api_url = "https://api.visorjpg.lat"
    genre_options: tuple[tuple[str, str], ...] = ()
    date_format = "dd/MM/yyyy"
    date_locale = "es"

    def __init__(self, fetcher=None) -> None:
        super().__init__(fetcher)
        self._latest_cursors: dict[int, str | None] = {}
        self._search_cursors: dict[tuple[int, str, str], str | None] = {}
        self.image_headers = {"Referer": f"{self.base_url}/"}

    def get_filters(self) -> list[SourceFilter]:
        return [SourceFilter("genres", "Géneros", "multi_select", list(self.genre_options), [])]

    async def browse(self, kind: str, page: int = 1):
        if kind == "popular":
            response = await self._request("GET", f"{self.api_url}/home/trending")
        elif kind == "latest":
            cursor = self._latest_cursors.get(page - 1)
            if cursor is None:
                cursor = self._latest_cursor()
            response = await self._request(
                "GET", f"{self.api_url}/home/lastest-updates", params={"cursor": cursor},
            )
        else:
            return {"items": [], "has_more": False}
        response.raise_for_status()
        payload = response.json() if hasattr(response, "json") else json.loads(response.text)
        if kind == "latest":
            self._latest_cursors[page] = payload.get("next_cursor")
        return {
            "items": [self._manga(item) for item in payload.get("data", [])],
            "has_more": kind == "latest" and payload.get("next_cursor") is not None,
        }

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        genres = ",".join(str(value) for value in (filters or {}).get("genres", []))
        cursor = self._search_cursors.get((page - 1, query, genres)) or ""
        params = {"cursor": cursor, "name": query}
        if genres:
            params["genres"] = genres
        response = await self._request("GET", f"{self.api_url}/search", params=params)
        response.raise_for_status()
        payload = response.json() if hasattr(response, "json") else json.loads(response.text)
        self._search_cursors[(page, query, genres)] = payload.get("next_cursor")
        return {
            "items": [self._manga(item) for item in payload.get("data", [])],
            "has_more": payload.get("next_cursor") is not None,
        }

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        slug = urlparse(series_id).path.rstrip("/").rsplit("/", 1)[-1]
        response = await self._request("GET", f"{self.base_url}/series/{slug}")
        response.raise_for_status()
        root = _parse_html(response.text)
        title = _first(
            root,
            lambda node: node.tag == "h1" and node.parent is not None and node.parent.has_class("grid"),
        )
        cover = _first(root, lambda node: node.tag == "div" and node.has_class("bg_main") and node.has_class("bg-cover"))
        cover_match = re.search(r"url\((.*?)\)", cover.attrs.get("style", "")) if cover else None
        paragraphs = [
            node.text().strip() for node in root.descendants("p")
            if node.parent is not None and node.parent.has_class("container")
            and node.parent.parent is not None and node.parent.parent.has_class("grid")
            and node.text().strip()
        ]
        status = ""
        label = _first(root, lambda node: node.tag == "span" and "status" in node.text().casefold())
        if label is not None:
            grid = self._ancestor(label, "grid")
            direct_divs = [node for node in grid.children if isinstance(node, _Node) and node.tag == "div"] if grid else []
            status = direct_divs[-1].text().strip() if direct_divs else ""
        genres = tuple(
            span.text().strip() for anchor in root.descendants("a")
            if "/series?genres" in anchor.attrs.get("href", "")
            if (span := _first(anchor, lambda node: node.tag == "span")) is not None and span.text().strip()
        )
        return SourceSeries(
            source_id=slug,
            title=title.text().strip() if title else slug,
            source_name=self.name,
            cover_url=cover_match.group(1).strip('"\'') if cover_match else None,
            description=" ".join(paragraphs) or None,
            status={"on-going": "ongoing", "end": "completed"}.get(status.casefold()),
            content_tags=genres,
            web_url=str(response.url),
        )

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        slug = urlparse(series_id).path.rstrip("/").rsplit("/", 1)[-1]
        response = await self._request("GET", f"{self.base_url}/series/{slug}")
        response.raise_for_status()
        result: list[SourceChapter] = []
        for anchor in _parse_html(response.text).descendants("a"):
            if not anchor.has_class("group") or anchor.parent is None or not anchor.parent.has_class("grid"):
                continue
            title = _first(anchor, lambda node: node.tag == "span" and node.has_class("truncate"))
            date = _first(anchor, lambda node: node.tag == "span" and node.has_class("w-fit"))
            url = urljoin(str(response.url), anchor.attrs.get("href", ""))
            parsed = urlparse(url)
            name = title.text().strip() if title else "Capítulo"
            number = re.search(r"\d+(?:\.\d+)?", name)
            result.append(SourceChapter(
                source_id=urlunparse(("", "", parsed.path, parsed.params, parsed.query, "")),
                title=name,
                series_id=slug,
                source_name=self.name,
                number=float(number.group()) if number else None,
                language=self.language,
                uploaded_at=self._madara_date(date.text() if date else ""),
            ))
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else str(chapter)
        response = await self._request("GET", urljoin(f"{self.base_url}/", chapter_id))
        response.raise_for_status()
        scripts = "\n".join(
            node.text() for node in _parse_html(response.text).descendants("script") if "svelteKit" in node.text()
        )
        found = re.search(r"images:(\[.*?])", scripts)
        urls = json.loads(found.group(1)) if found else []
        return [SourcePage(
            source_id=str(url),
            chapter_id=chapter_id,
            index=index,
            filename=urlparse(str(url)).path.rsplit("/", 1)[-1] or f"{index}.jpg",
            source_name=self.name,
        ) for index, url in enumerate(urls, 1)]

    def _manga(self, item: dict) -> SourceSeries:
        slug = str(item.get("slug", ""))
        return SourceSeries(
            source_id=slug,
            title=str(item.get("name", "")),
            source_name=self.name,
            cover_url=str(item.get("cover_url") or "") or None,
            web_url=f"{self.base_url}/series/{slug}",
        )

    @staticmethod
    def _latest_cursor() -> str:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        payload = {"last_update_at": now, "id": 0, "_pointsToNextItems": True}
        return base64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()

    @staticmethod
    def _ancestor(node: _Node, class_name: str) -> _Node | None:
        parent = node.parent
        while parent is not None:
            if parent.has_class(class_name):
                return parent
            parent = parent.parent
        return None


class MangoLibreriaSource(GenericSource):
    strip_external_image_referer = True

    async def browse(self, kind: str, page: int = 1):
        if kind not in {"popular", "latest"}:
            return {"items": [], "has_more": False}
        params = {"page": str(page)}
        if kind == "popular":
            params["sort"] = "views"
        return await self._listing(params)

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        params = {"page": str(page)}
        params["q" if query.strip() else "sort"] = query.strip() or "views"
        return await self._listing(params)

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        item = self._props(response, "comicData")
        return SourceSeries(
            source_id=series_id,
            title=str(item.get("title") or item.get("name") or series_id),
            source_name=self.name,
            cover_url=str(item.get("urlCover") or item.get("cover_image") or "") or None,
            description=str(item.get("description") or "").strip() or None,
            status=self._status(item.get("state")),
            content_tags=tuple(
                str(genre.get("name", "")).strip()
                for genre in item.get("genres") or []
                if str(genre.get("name", "")).strip()
            ),
            web_url=str(response.url),
        )

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        item = self._props(response, "comicData")
        result: list[SourceChapter] = []
        for group in item.get("scan_groups") or []:
            for chapter in group.get("chapters") or []:
                raw_number = str(chapter.get("chapter_number", ""))
                try:
                    number = float(raw_number)
                except ValueError:
                    number = None
                display_number = raw_number.removesuffix(".0")
                result.append(SourceChapter(
                    source_id=str(chapter.get("chapter_path") or ""),
                    title=str(chapter.get("title") or f"Capítulo {display_number}"),
                    series_id=series_id,
                    source_name=self.name,
                    number=number,
                    scanlator=str(group.get("name") or ""),
                    language=self.language,
                    uploaded_at=self._date(str(chapter.get("release_date") or chapter.get("created_at") or "")),
                ))
        return sorted(result, key=lambda chapter: chapter.number if chapter.number is not None else -1, reverse=True)

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else str(chapter)
        response = await self._request("GET", urljoin(f"{self.base_url}/", chapter_id))
        response.raise_for_status()
        urls = self._props(response, "comicData").get("url_pages") or []
        return [SourcePage(
            source_id=str(url),
            chapter_id=chapter_id,
            index=index,
            filename=urlparse(str(url)).path.rsplit("/", 1)[-1] or f"{index}.jpg",
            source_name=self.name,
        ) for index, url in enumerate(urls)]

    async def _listing(self, params: dict[str, str]):
        response = await self._request("GET", f"{self.base_url}/comics", params=params)
        response.raise_for_status()
        data = self._props(response, "comicsData")
        return {
            "items": [self._manga(item) for item in data.get("comics") or []],
            "has_more": int(data.get("page", 1)) < int(data.get("totalPages", 1)),
        }

    def _manga(self, item: dict) -> SourceSeries:
        source_id = str(item.get("urlPath") or item.get("comic_path") or "")
        genres = item.get("genres") or []
        return SourceSeries(
            source_id=source_id,
            title=str(item.get("name") or ""),
            source_name=self.name,
            cover_url=str(item.get("urlCover") or item.get("cover_image") or "") or None,
            description=str(item.get("description") or "").strip() or None,
            status=self._status(item.get("state")),
            content_tags=tuple(str(genre) for genre in genres),
            web_url=urljoin(f"{self.base_url}/", source_id),
        )

    @staticmethod
    def _status(value) -> str | None:
        return {
            "ONGOING": "ongoing", "COMPLETED": "completed", "HIATUS": "hiatus",
            "CANCELLED": "cancelled",
        }.get(str(value or "").upper())

    @staticmethod
    def _date(value: str) -> str | None:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
        except ValueError:
            return None

    @classmethod
    def _props(cls, response, key: str):
        scripts = [node.text() for node in _parse_html(response.text).descendants("script")]
        candidates = list(scripts)
        for script in scripts:
            match = re.search(r"self\.__next_f\.push\((.*)\)\s*;?\s*$", script, re.S)
            if match:
                try:
                    candidates.extend(cls._strings(json.loads(match.group(1))))
                except json.JSONDecodeError:
                    pass
        decoder = json.JSONDecoder()
        for text in candidates:
            for start, char in enumerate(text):
                if char != "{":
                    continue
                try:
                    value, _ = decoder.raw_decode(text, start)
                except json.JSONDecodeError:
                    continue
                found = cls._find(value, key)
                if found is not None:
                    return found
        raise ValueError(f"No se encontró {key} en los datos de Next.js")

    @classmethod
    def _find(cls, value, key: str):
        if isinstance(value, dict):
            if key in value:
                return value[key]
            return next((found for item in value.values() if (found := cls._find(item, key)) is not None), None)
        if isinstance(value, list):
            return next((found for item in value if (found := cls._find(item, key)) is not None), None)
        return None

    @classmethod
    def _strings(cls, value):
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            return [text for item in value.values() for text in cls._strings(item)]
        if isinstance(value, list):
            return [text for item in value for text in cls._strings(item)]
        return []


class LmtosSource(MangoLibreriaSource):
    genre_options: tuple[tuple[str, str], ...] = ()
    status_options: tuple[tuple[str, str], ...] = ()
    demographic_options: tuple[tuple[str, str], ...] = ()
    type_options: tuple[tuple[str, str], ...] = ()
    nsfw_options: tuple[tuple[str, str], ...] = ()
    order_options: tuple[tuple[str, str], ...] = ()

    def __init__(self, fetcher=None) -> None:
        super().__init__(fetcher)
        self._catalog: list[dict] = []
        self._catalog_at = 0.0
        self._catalog_lock = asyncio.Lock()

    def get_filters(self) -> list[SourceFilter]:
        return [
            SourceFilter("genres", "Géneros", "multi_select", list(self.genre_options), []),
            SourceFilter("status", "Estado", "select", list(self.status_options), ""),
            SourceFilter("demographic", "Demografía", "select", list(self.demographic_options), ""),
            SourceFilter("type", "Tipo", "select", list(self.type_options), ""),
            SourceFilter("nsfw", "+18", "select", list(self.nsfw_options), ""),
            SourceFilter("order", "Orden", "select", list(self.order_options), "a-z"),
        ]

    async def browse(self, kind: str, page: int = 1):
        if kind == "latest":
            return await self.search("", page, {"order": "recents"})
        if kind != "popular":
            return {"items": [], "has_more": False}
        response = await self._request("GET", f"{self.base_url}/destacados")
        response.raise_for_status()
        root = _parse_html(response.text)
        result: list[SourceSeries] = []
        for anchor in root.descendants("a"):
            if not anchor.has_class("group") or anchor.parent is None or anchor.parent.tag != "section":
                continue
            heading = _first(anchor, lambda node: node.tag == "h3")
            if heading is None:
                continue
            image = _first(anchor, lambda node: node.tag == "img")
            slug = urlparse(anchor.attrs.get("href", "")).path.rstrip("/").rsplit("/", 1)[-1]
            result.append(SourceSeries(
                source_id=slug,
                title=heading.text().strip(),
                source_name=self.name,
                cover_url=_image_url(image, str(response.url)) if image else None,
                web_url=f"{self.base_url}/manga/{slug}",
            ))
        return {"items": result, "has_more": False}

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        values = filters or {}
        genres = {str(value) for value in values.get("genres", [])}
        needle = query.casefold().strip()
        items = [
            item for item in await self._fetch_catalog()
            if not needle or needle in str(item.get("title", "")).casefold()
            or any(needle in str(title).casefold() for title in item.get("alternativeTitles") or [])
        ]
        nsfw = str(values.get("nsfw", ""))
        if nsfw == "only":
            items = [item for item in items if item.get("isAdult")]
        elif nsfw == "hide":
            items = [item for item in items if not item.get("isAdult")]
        for key in ("type", "status", "demographic"):
            if selected := values.get(key):
                items = [item for item in items if item.get(key) == selected]
        if genres:
            items = [item for item in items if genres.issubset(set(item.get("genres") or []))]
        order = str(values.get("order", "a-z"))
        if order == "a-z":
            items.sort(key=lambda item: str(item.get("title", "")))
        elif order == "recents":
            items.sort(key=lambda item: str(item.get("latestChapterCreatedAt") or ""), reverse=True)
        elif order == "views":
            items.sort(key=lambda item: int(item.get("totalViews") or 0), reverse=True)
        start = (page - 1) * 20
        return {
            "items": [self._series(item) for item in items[start:start + 20]],
            "has_more": len(items) > start + 20,
        }

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        slug = self._slug(series_id)
        response = await self._request("GET", f"{self.base_url}/manga/{slug}")
        response.raise_for_status()
        return self._series(self._props(response, "manga"))

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        slug = self._slug(series_id)
        response = await self._request("GET", f"{self.base_url}/manga/{slug}")
        response.raise_for_status()
        manga_slug = str(self._props(response, "manga").get("slug") or slug)
        result: list[SourceChapter] = []
        for item in self._props(response, "chapters"):
            number = float(item.get("number", -1))
            result.append(SourceChapter(
                source_id=f"{manga_slug}/{item.get('slug', '')}",
                title=f"Cap. {str(number).removesuffix('.0')}",
                series_id=manga_slug,
                source_name=self.name,
                number=number,
                language=self.language,
                uploaded_at=self._date(str(item.get("createdAt") or "")),
            ))
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else str(chapter)
        response = await self._request("GET", f"{self.base_url}/manga/{chapter_id.strip('/')}")
        response.raise_for_status()
        urls = self._props(response, "chapter").get("pages") or []
        return [SourcePage(
            source_id=str(url),
            chapter_id=chapter_id,
            index=index,
            filename=urlparse(str(url)).path.rsplit("/", 1)[-1] or f"{index}.jpg",
            source_name=self.name,
        ) for index, url in enumerate(urls)]

    async def _fetch_catalog(self) -> list[dict]:
        if self._catalog and time.monotonic() - self._catalog_at < 600:
            return self._catalog
        async with self._catalog_lock:
            if self._catalog and time.monotonic() - self._catalog_at < 600:
                return self._catalog
            response = await self._request("GET", f"{self.base_url}/series")
            response.raise_for_status()
            self._catalog = list(self._props(response, "mangas"))
            self._catalog_at = time.monotonic()
        return self._catalog

    def _series(self, item: dict) -> SourceSeries:
        slug = str(item.get("slug") or "")
        description = str(item.get("description") or "").strip()
        alternatives = [str(value) for value in item.get("alternativeTitles") or []]
        if alternatives:
            description = f"{description}\n\nNombres alternativos: {', '.join(alternatives)}".strip()
        kind = str(item.get("type") or "")
        tags = ([kind[:1].upper() + kind[1:]] if kind else []) + [str(value) for value in item.get("genres") or []]
        return SourceSeries(
            source_id=slug,
            title=str(item.get("title") or slug),
            source_name=self.name,
            cover_url=str(item.get("coverImage") or "") or None,
            description=description or None,
            author=str(item.get("author") or "") or None,
            artist=str(item.get("artist") or "") or None,
            status={"ongoing": "ongoing", "completed": "completed", "paused": "hiatus"}.get(str(item.get("status") or "").casefold()),
            content_tags=tuple(tags),
            web_url=f"{self.base_url}/manga/{slug}",
        )

    @staticmethod
    def _slug(value: str) -> str:
        parts = [part for part in urlparse(value).path.split("/") if part]
        return parts[-1] if parts else value


class HentaiModeSource(GenericSource):
    supports_latest = False

    def __init__(self, fetcher=None) -> None:
        super().__init__(fetcher)
        self.image_headers = {"Referer": f"{self.base_url}/"}

    async def browse(self, kind: str, page: int = 1):
        if kind != "popular":
            return {"items": [], "has_more": False}
        response = await self._request("GET", self.base_url)
        response.raise_for_status()
        return {"items": self._mangas(response), "has_more": False}

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        if query.startswith("https://"):
            url = urlparse(query)
            if url.hostname != urlparse(self.base_url).hostname:
                raise ValueError("URL no compatible")
            parts = [part for part in url.path.split("/") if part]
            if len(parts) < 2:
                raise ValueError("URL de HentaiMode no válida")
            query = f"id:{parts[1]}"
        if query.startswith("id:"):
            response = await self._request("GET", f"{self.base_url}/g/{query[3:]}")
            response.raise_for_status()
            return {"items": [self._details(response)], "has_more": False}
        if len(query) < 3:
            raise ValueError("La búsqueda debe tener al menos 3 caracteres")
        response = await self._request("GET", f"{self.base_url}/buscar", params={"s": query})
        response.raise_for_status()
        return {"items": self._mangas(response), "has_more": False}

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        return self._details(response, series_id)

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        return [SourceChapter(
            source_id=series_id.replace("/g/", "/leer/"),
            title="Chapter",
            series_id=series_id,
            source_name=self.name,
            number=1.0,
            language=self.language,
        )]

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else str(chapter)
        response = await self._request("GET", urljoin(f"{self.base_url}/", chapter_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        script = next(
            (node.text() for node in root.descendants("script") if "page_image" in node.text()),
            None,
        )
        if script is None:
            raise ValueError("Script de páginas no encontrado")
        urls = re.findall(r'''["']?page_image["']?\s*:\s*["']([^"']+)["']''', script)
        return [SourcePage(
            source_id=url,
            chapter_id=chapter_id,
            index=index,
            filename=urlparse(url).path.rsplit("/", 1)[-1] or f"{index}.jpg",
            source_name=self.name,
        ) for index, url in enumerate(urls, 1)]

    def _mangas(self, response) -> list[SourceSeries]:
        root = _parse_html(response.text)
        result: list[SourceSeries] = []
        for anchor in root.descendants("a"):
            holder = anchor.parent
            if holder is None or holder.tag != "div" or "book-list" not in holder.attrs.get("class", ""):
                continue
            parent = holder.parent
            if not any(node.tag == "div" and node.has_class("row") for node in self._parents(parent)):
                continue
            title = _first(anchor, lambda node: node.tag == "p" and self._has_class_ancestor(node, "book-description"))
            if title is None:
                continue
            image = _first(anchor, lambda node: node.tag == "img")
            source_id = urljoin(str(response.url), anchor.attrs.get("href", ""))
            result.append(SourceSeries(
                source_id=source_id,
                title=title.text().strip(),
                source_name=self.name,
                cover_url=_image_url(image, str(response.url)) if image else None,
                web_url=source_id,
            ))
        return result

    def _details(self, response, source_id: str | None = None) -> SourceSeries:
        root = _parse_html(response.text)
        info = _first(
            root,
            lambda node: node.tag == "div" and node.attrs.get("id") == "info"
            and node.parent is not None and node.parent.attrs.get("id") == "info-block",
        )
        if info is None:
            raise ValueError("Bloque de información no encontrado")
        title = _first(info, lambda node: node.tag == "h1")
        if title is None:
            raise ValueError("Título no encontrado")
        image = _first(root, lambda node: node.tag == "img" and self._has_id_ancestor(node, "cover"))
        extra = [
            f"{label}: {', '.join(values)}"
            for label in ("Serie", "Tipo", "Personajes", "Idioma")
            if (values := self._info(info, label))
        ]
        genres = self._info(info, "Categorías")
        authors = self._info(info, "Grupo")
        artists = self._info(info, "Artista")
        identifier = source_id or str(response.url)
        return SourceSeries(
            source_id=identifier,
            title=title.text().strip(),
            source_name=self.name,
            cover_url=_image_url(image, str(response.url)) if image else None,
            description="\n".join(extra) or None,
            author=", ".join(authors) or None,
            artist=", ".join(artists) or None,
            status="completed",
            content_tags=tuple(genres),
            metadata={"update_strategy": "only_fetch_once"},
            web_url=str(response.url),
        )

    @staticmethod
    def _info(info: _Node, label: str) -> list[str]:
        for node in info.descendants("div"):
            own_text = " ".join(
                child.strip() for child in node.children if isinstance(child, str) and child.strip()
            )
            if node.has_class("tag-container") and label in own_text:
                return [
                    anchor.text().strip() for anchor in node.descendants("a")
                    if anchor.has_class("tag") and anchor.text().strip()
                ]
        return []

    @staticmethod
    def _parents(node: _Node | None):
        while node is not None:
            yield node
            node = node.parent

class GeneratedGenericSource(GenericSource):
    name = 'mangahub_ru'
    display_name = 'Mangahub'
    base_url = 'https://mangahub.ru'
    language = 'ru'
    requests_per_minute = 120
    content_warning = 'mixed'


SOURCE = GeneratedGenericSource

"""Puente de contrato para adaptadores que conservan metodos v3."""

import inspect
from collections.abc import Mapping
from typing import Any

from nyanko_api.sources.contract import Paginated, SourceFilter, SourcePreference

_PAGE_SIZE = 20


def _parameters(method: Any) -> Mapping[str, Any]:
    return inspect.signature(method).parameters


def _arguments(method: Any, page: int, filters: Mapping[str, Any] | None) -> dict[str, Any]:
    parameters = _parameters(method)
    arguments: dict[str, Any] = {}
    if "page" in parameters:
        arguments["page"] = page
    if "filters" in parameters:
        arguments["filters"] = filters
    if "limit" in parameters:
        # Un metodo v3 sin `page` solo se controla por `limit`: se pide el
        # acumulado hasta la pagina solicitada y luego se recorta el tramo. El
        # elemento extra es el sondeo que distingue "no hay mas" de "justo cabia".
        arguments["limit"] = _PAGE_SIZE if "page" in parameters else page * _PAGE_SIZE + 1
    return arguments


def _unwrap(value: Any) -> tuple[list[Any], bool | None]:
    """Normaliza un retorno v3 a ``(items, has_more)``; ``None`` si no lo declara."""
    if isinstance(value, Paginated):
        return list(value.items), value.has_more
    if isinstance(value, dict):
        declared = value.get("has_more", value.get("has_next_page"))
        items = value.get("items", value.get("results", []))
        return list(items or []), None if declared is None else bool(declared)
    return list(value or []), None


def _paginated(value: Any, has_more: bool) -> Paginated:
    items, declared = _unwrap(value)
    if declared is not None:
        has_more = declared
    return Paginated(items=items, has_more=has_more and bool(items))


def _window(value: Any, page: int) -> Paginated:
    """Pagina en el cliente un metodo v3 que devuelve el acumulado de una vez."""
    items, declared = _unwrap(value)
    start = (page - 1) * _PAGE_SIZE
    window = items[start : start + _PAGE_SIZE]
    has_more = len(items) > start + _PAGE_SIZE if declared is None else declared
    return Paginated(items=window, has_more=has_more and bool(window))


def _consumes_filters(legacy_source: type) -> bool:
    return any(
        "filters" in _parameters(method)
        for name in ("search", "browse")
        if callable(method := getattr(legacy_source, name, None))
    )


def _options(options: Any) -> list[tuple[str, str]] | None:
    if options is None:
        return None
    return [
        (str(option.get("value", "")), str(option.get("name", "")))
        if isinstance(option, dict)
        else (str(option[0]), str(option[1]))
        for option in options
    ]


def _filters(values: Any) -> list[SourceFilter]:
    return [
        SourceFilter(
            id=value.id,
            name=value.name,
            type="multi_select" if value.type == "group" else value.type,
            options=_options(value.options),
            default=[] if value.type == "group" and not isinstance(value.default, list) else value.default,
        )
        for value in values
    ]


def _preferences(values: Any) -> list[SourcePreference]:
    return [
        SourcePreference(
            id=value.id,
            name=value.name,
            type=value.type,
            options=_options(value.options),
            default=value.default,
        )
        for value in values
    ]


def adapt_source(legacy_source: type) -> type:
    # Un filtro que ningun metodo v3 acepta no se anuncia: la UI mostraria
    # controles que el adaptador descarta en silencio.
    publishes_filters = _consumes_filters(legacy_source)

    class SourceV4(legacy_source):
        async def get_filters(self) -> list[SourceFilter]:
            getter = getattr(super(), "get_filters", None)
            if not getter or not publishes_filters:
                return []
            values = getter()
            if inspect.isawaitable(values):
                values = await values
            return _filters(values)

        def get_preferences(self) -> list[SourcePreference]:
            getter = getattr(super(), "get_preferences", None)
            return _preferences(getter()) if getter else []

        async def search(
            self,
            query: str,
            page: int = 1,
            filters: Mapping[str, Any] | None = None,
        ) -> Paginated:
            method = super().search
            result = await method(query, **_arguments(method, page, filters))
            if "page" in _parameters(method):
                return _paginated(result, True)
            return _window(result, page)

        async def browse(
            self,
            kind: str,
            page: int = 1,
            filters: Mapping[str, Any] | None = None,
        ) -> Paginated:
            method = super().browse
            return _paginated(await method(kind, **_arguments(method, page, filters)), True)

    SourceV4.__name__ = legacy_source.__name__
    SourceV4.__qualname__ = legacy_source.__qualname__
    return SourceV4

SOURCE = adapt_source(SOURCE)
