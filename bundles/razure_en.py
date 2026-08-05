"""Implementación común del tema Madara para bundles Nyanko Source v3."""

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
    return urljoin(base_url, candidates[-1]) if candidates else ""


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
            image = _first(item, lambda node: node.tag == "img")
            result.append(
                SourceSeries(
                    source_id=source_id,
                    title=title,
                    source_name=self.name,
                    cover_url=_image_url(image, self.base_url) if image else None,
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
                image = _first(anchor, lambda node: node.tag == "img")
                result.append(
                    SourceSeries(
                        source_id=source_id,
                        title=title,
                        source_name=self.name,
                        cover_url=_image_url(image, self.base_url) if image else None,
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

"""Implementación común del tema MangaThemesia para Nyanko Source v3."""

import ast
import base64
import io
import json
import re
from urllib.parse import unquote, urljoin, urlparse, urlunparse

from PIL import Image

try:
    from .madara import (
        MadaraSource,
        SourceChapter,
        SourceFilter,
        SourcePage,
        SourcePageContent,
        SourceSeries,
        SourceNotFoundError,
        _first,
        _image_url,
        _parse_html,
    )
except ImportError:
    # Al generar un bundle este archivo se concatena después de madara.py.
    pass


class MangaThemesiaSource(MadaraSource):
    manga_directory = "/manga"
    reader_id = "readerarea"
    supports_latest = True
    image_no_referer_hosts: tuple[str, ...] = ()
    search_profile = "default"
    pages_profile = "default"
    reader_class = ""
    image_class = ""
    chapter_profile = "default"
    browse_profile = "default"
    page_element_classes: tuple[str, ...] = ()
    request_referer = ""
    accept_language = ""
    project_directory = ""

    def __init__(self, fetcher=None) -> None:
        super().__init__(fetcher)
        if self.request_referer:
            self.capabilities.headers["Referer"] = self.request_referer
        if self.accept_language:
            self.capabilities.headers["Accept-Language"] = self.accept_language

    async def page_bytes(self, page: SourcePage | str) -> SourcePageContent:
        url = page.source_id if isinstance(page, SourcePage) else page
        if not url:
            raise SourceNotFoundError("Página MangaThemesia sin URL")
        parsed = urlparse(url)
        host = parsed.hostname or ""
        headers = {} if any(value in host for value in self.image_no_referer_hosts) else {
            "Referer": page.chapter_id if isinstance(page, SourcePage) else self.base_url
        }
        response = await self._request("GET", urlunparse(parsed._replace(fragment="")), headers=headers)
        response.raise_for_status()
        content = response.content
        if self.pages_profile == "mangakimi" and parsed.fragment:
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

    def get_filters(self) -> list[SourceFilter]:
        return [
            SourceFilter("projects", "Solo proyectos", "checkbox", default=False)
        ] if self.project_directory else []

    async def search(
        self,
        query: str,
        page: int = 1,
        filters: dict | None = None,
    ) -> list[SourceSeries]:
        profile = self.search_profile
        if profile == "rizz":
            response = await self._request(
                "POST",
                f"{self.base_url}/Index/live_search",
                data={"search_value": query.strip()},
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
            response.raise_for_status()
            return self._rizz_series(response.json())[:limit]
        path = f"{self.project_directory if (filters or {}).get('projects') else self.manga_directory.rstrip('/')}/"
        params = {"title": query.strip(), "page": str(page)}
        if profile == "comic_asura":
            path, params = "/advanced-search", {"name": query.strip(), "page": "1"}
        elif profile == "s":
            params = {"s": query.strip(), "page": "1"}
        elif profile == "search":
            params = {"search": query.strip(), "page": "1"}
        elif profile == "sushi":
            path, params = "/page/1", {"s": query.strip()}
        elif profile == "ngomik":
            params = {"title": query.strip(), "page": "1"}
        elif profile == "starlight":
            path, params = "/buscar", {"search": query.strip(), "page-current": "1"}
        elif profile == "mangacan":
            slug = re.sub(r"\s+", "-", query.strip().lower())
            path, params = f"/cari/{slug}/1.html", {}
        elif profile == "rokari":
            path, params = "/", {"s": query.strip(), "page": "1"}
        return await self._listing(params, path=path)

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind == "latest" and not self.supports_latest:
            return []
        if kind not in {"popular", "latest"}:
            return []
        if self.browse_profile == "rizz":
            response = await self._request(
                "POST",
                f"{self.base_url}/Index/filter_series",
                data={"OrderValue": "popular" if kind == "popular" else "update"},
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
            response.raise_for_status()
            return self._rizz_series(response.json())
        if self.browse_profile == "rokari":
            if kind == "popular" and page > 1:
                return []
            path = "" if page == 1 else f"/page/{page}/"
            response = await self._request("GET", f"{self.base_url}{path}")
            response.raise_for_status()
            return self._rokari_series(response.text, str(response.url), kind)
        return await self._listing(
            {"title": "", "page": str(page), "order": "popular" if kind == "popular" else "update"}
        )

    async def _listing(
        self,
        params: dict[str, str],
        *,
        path: str | None = None,
    ) -> list[SourceSeries]:
        response = await self._request(
            "GET",
            f"{self.base_url}{path or self.manga_directory.rstrip('/') + '/'}",
            params=params,
        )
        response.raise_for_status()
        root = _parse_html(response.text)
        result: list[SourceSeries] = []
        seen: set[str] = set()
        for item in root.descendants():
            if not (
                item.has_class("imgu")
                or item.has_class("bsx")
                or item.has_class("manga-card-v")
                or item.has_class("bulkMangaCard")
                or item.has_class("legend-inner")
                or item.tag == "a"
                and self.manga_directory.rstrip("/") in item.attrs.get("href", "")
            ):
                continue
            anchor = _first(item, lambda node: node.tag == "a" and bool(node.attrs.get("href")))
            if anchor is None:
                continue
            source_id = urljoin(f"{self.base_url}/", anchor.attrs["href"])
            title = anchor.attrs.get("title", "").strip() or anchor.text().strip()
            if not title:
                image = _first(anchor, lambda node: node.tag == "img")
                title = image.attrs.get("alt", "").strip() if image else ""
            if source_id in seen or not title:
                continue
            seen.add(source_id)
            image = _first(item, lambda node: node.tag == "img")
            result.append(SourceSeries(
                source_id=source_id, title=title, source_name=self.name,
                cover_url=_image_url(image, str(response.url)) if image else None,
                web_url=source_id,
            ))
        return result

    def _rizz_series(self, payload: list[dict]) -> list[SourceSeries]:
        result: list[SourceSeries] = []
        for item in payload:
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            slug = re.sub(r"[^a-z0-9]+", "-", title.lower().replace("'", "")).strip("-")
            slug = re.sub(r"^(r\d+-)", "", slug).replace("-s-", "s-").replace("-ll-", "ll-")
            result.append(
                SourceSeries(
                    source_id=f"{self.base_url}{self.manga_directory}/{slug}/#{item.get('id', '')}",
                    title=title,
                    source_name=self.name,
                )
            )
        return result

    def _rokari_series(self, html: str, response_url: str, kind: str) -> list[SourceSeries]:
        root = _parse_html(html)
        wanted = "popular" if kind == "popular" else "latest"
        result: list[SourceSeries] = []
        for item in root.descendants():
            if not item.has_class("bsx"):
                continue
            section = item.parent
            while section is not None and not section.has_class("bixbox"):
                section = section.parent
            heading = _first(section, lambda node: node.tag == "h2") if section else None
            if heading is None or wanted not in heading.text().lower():
                continue
            anchor = _first(item, lambda node: node.tag == "a" and bool(node.attrs.get("href")))
            if anchor is None:
                continue
            title = anchor.attrs.get("title", "").strip() or anchor.text().strip()
            if title:
                result.append(
                    SourceSeries(
                        source_id=urljoin(response_url, anchor.attrs["href"]),
                        title=title,
                        source_name=self.name,
                    )
                )
        return result

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        series_url = urljoin(f"{self.base_url}/", series_id)
        response = await self._request(
            "POST" if self.chapter_profile == "astral" else "GET",
            series_url,
            files={"manga_req": (None, "ping")} if self.chapter_profile == "astral" else None,
            headers={"X-Requested-With": "XMLHttpRequest"} if self.chapter_profile == "astral" else None,
        )
        response.raise_for_status()
        text = response.text
        dynamic_attribute = ""
        if self.chapter_profile == "astral" and text.startswith("ASTRAL_"):
            parts = text.split("|||")
            if len(parts) >= 3:
                text = base64.b64decode(parts[1]).decode()
                dynamic_attribute = parts[2]
        root = _parse_html(text)
        result: list[SourceChapter] = []
        seen: set[str] = set()
        for item in root.descendants():
            if dynamic_attribute:
                if not item.attrs.get(dynamic_attribute) or item.has_class("trap"):
                    continue
                try:
                    href = base64.b64decode(item.attrs[dynamic_attribute]).decode()
                except (ValueError, UnicodeDecodeError):
                    continue
                anchor = item
                anchor.attrs["href"] = href
                chapter_label = _first(
                    item,
                    lambda node: node.tag == "span"
                    and any(value.startswith("n_") for value in node.attrs.get("class", "").split()),
                )
            elif item.tag != "li" and not (
                item.tag == "div"
                and (
                    item.has_class("ch-item")
                    or item.has_class("chapter-items")
                    or item.has_class("astral-item")
                    or item.has_class("mangaDetails__episode")
                )
            ):
                continue
            else:
                anchor = _first(item, lambda node: node.tag == "a" and bool(node.attrs.get("href")))
                chapter_label = _first(
                    item,
                    lambda node: node.has_class("chapternum")
                    or node.has_class("lch")
                    or node.has_class("eph-num"),
                )
            if anchor is None:
                continue
            source_id = urljoin(series_url, anchor.attrs["href"])
            if source_id in seen:
                continue
            title = chapter_label.text().strip() if chapter_label else anchor.text().strip()
            if not title or not re.search(r"\d|chapter|cap|ch|epis[oó]dio", title, re.I):
                continue
            seen.add(source_id)
            match = re.search(r"(?:chapter|cap(?:í|i)tulo|ch)[^\d]*(\d+(?:\.\d+)?)", title, re.I)
            if match is None:
                match = re.search(r"(\d+(?:\.\d+)?)", title)
            date = _first(item, lambda node: node.has_class("chapterdate"))
            result.append(
                SourceChapter(
                    source_id=source_id,
                    title=title or "Capítulo",
                    series_id=series_id,
                    source_name=self.name,
                    number=float(match.group(1)) if match else None,
                    uploaded_at=self._madara_date(date.text() if date else ""),
                )
            )
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        response = await self._request("GET", urljoin(f"{self.base_url}/", chapter_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        if self.pages_profile == "area_api":
            chapter_input = _first(root, lambda node: node.attrs.get("id") == "comment_post_ID")
            chapter_value = chapter_input.attrs.get("value", "") if chapter_input else ""
            if not chapter_value:
                raise ValueError("No se encontró el ID del capítulo")
            api_response = await self._request(
                "POST",
                f"{self.base_url}/wp-admin/admin-ajax.php",
                data={"action": "get_secure_chapter_images", "chapter_id": chapter_value},
                headers={
                    "Referer": str(response.url),
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            api_response.raise_for_status()
            payload = api_response.json()
            data = payload.get("data") or {}
            if data.get("status") == "locked":
                raise ValueError("Capítulo bloqueado; requiere sesión WebView")
            root = _parse_html(data.get("content") or "")
        if self.pages_profile == "mangakimi":
            urls = [
                _image_url(image, str(response.url))
                for image in root.descendants("img")
                if self._has_ancestor_id(image, self.reader_id)
            ]
            for script in root.descendants("script"):
                if "p,a,c,k,e,d" not in script.text():
                    continue
                unpacked = self._unpack_packer(script.text())
                width = re.search(r"""width:\s*["']?\s*\+?\s*(\d+)""", unpacked)
                height = re.search(r"""height:\s*["']?\s*\+?\s*(\d+)""", unpacked)
                matrix = re.search(r"(\[\s*\[.*?]])\s*;", unpacked, re.S)
                image_url = re.search(r"""url\((['"]?)(.*?)\1\);""", unpacked)
                if not all((width, height, matrix, image_url)):
                    continue
                data = {
                    "blockWidth": int(width.group(1)),
                    "blockHeight": int(height.group(1)),
                    "matrix": json.loads(matrix.group(1)),
                }
                urls.append(f"{urljoin(str(response.url), image_url.group(2))}#{json.dumps(data, separators=(',', ':'))}")
            if urls:
                return self._source_pages(urls, chapter_id)
        reader = _first(root, lambda node: node.attrs.get("id", "").lower() == self.reader_id.lower())
        if reader is None and self.reader_class:
            reader = _first(root, lambda node: node.has_class(self.reader_class))
        images = [
            image
            for image in (
                reader.descendants("img")
                if reader
                else root.descendants("img")
                if self.pages_profile in {"all_images", "area_api"}
                else []
            )
            if not self._has_ancestor_tag(image, "noscript")
            and (not self.image_class or image.has_class(self.image_class))
        ]
        if self.page_element_classes:
            urls = [
                _image_url(node, str(response.url))
                for node in root.descendants()
                if any(node.has_class(value) for value in self.page_element_classes)
            ]
        else:
            urls = list(
                dict.fromkeys(
                    url for image in images if (url := _image_url(image, str(response.url)))
                )
            )
        script_text = response.text
        encoded = re.search(
            r"""<script[^>]+src=["']data:text/javascript;base64,([^"']+)""",
            response.text,
            re.I,
        )
        if encoded:
            try:
                script_text += base64.b64decode(encoded.group(1)).decode()
            except (ValueError, UnicodeDecodeError):
                pass
        if not urls:
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
        if self.pages_profile == "mangatv":
            decoded: list[str] = []
            for url in urls:
                try:
                    value = base64.b64decode(url).decode()
                    decoded.append(f"https:{value}" if value.startswith("//") else value)
                except (ValueError, UnicodeDecodeError):
                    decoded.append(url)
            urls = decoded
        if self.pages_profile == "bloom":
            urls = [urljoin(f"{self.base_url}/", url) for url in urls]
        if self.pages_profile == "no_mihon":
            urls = [url for url in urls if "mihon" not in url.lower()]
        if self.pages_profile == "no_gif":
            urls = [url for url in urls if ".gif" not in url.lower()]
        return self._source_pages(urls, chapter_id)

    def _source_pages(self, urls: list[str], chapter_id: str) -> list[SourcePage]:
        return [
            SourcePage(
                source_id=url,
                chapter_id=chapter_id,
                index=index,
                filename=url.rsplit("/", 1)[-1].split("?", 1)[0] or f"{index}.jpg",
                source_name=self.name,
            )
            for index, url in enumerate(dict.fromkeys(urls), 1)
        ]

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

    @staticmethod
    def _has_ancestor_tag(node: object, tag: str) -> bool:
        parent = getattr(node, "parent", None)
        while parent is not None:
            if parent.tag == tag:
                return True
            parent = parent.parent
        return False

    @staticmethod
    def _has_ancestor_id(node: object, node_id: str) -> bool:
        parent = getattr(node, "parent", None)
        while parent is not None:
            if parent.attrs.get("id", "").lower() == node_id.lower():
                return True
            parent = parent.parent
        return False


class MangaTVSource(MangaThemesiaSource):
    async def search(self, query: str, page: int = 1, filters: dict | None = None) -> list[SourceSeries]:
        return await self._listing({"s": query.strip(), "page": str(page)})

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        return await self._listing({"s": "", "page": str(page)}) if kind in {"popular", "latest"} else []

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        root = _parse_html(response.text)

        def labeled(label: str):
            for node in root.descendants("div"):
                if not (node.has_class("wd-full") or node.has_class("imptdt")):
                    continue
                heading = _first(node, lambda item: item.tag == "b")
                own_text = " ".join(item.strip() for item in node.children if isinstance(item, str))
                if label in f"{own_text} {heading.text() if heading else ''}".casefold():
                    return node
            return None

        title = _first(root, lambda node: node.tag == "h1" and node.has_class("entry-title"))
        image = _first(root, lambda node: node.tag == "img" and self._has_class_ancestor(node, "thumb"))
        synopsis = labeled("sinopsis")
        description = _first(synopsis, lambda node: node.tag == "span") if synopsis else None
        status_row = labeled("estado")
        status = _first(status_row, lambda node: node.tag in {"i", "span"}) if status_row else None
        genre_row = labeled("generos")
        genres = [
            anchor.text().strip().capitalize()
            for anchor in genre_row.descendants("a") if anchor.text().strip()
        ] if genre_row else []
        type_row = labeled("tipo")
        type_node = _first(type_row, lambda node: node.tag in {"a", "i", "span"}) if type_row else None
        if type_node and type_node.text().strip():
            genres.append(type_node.text().strip())
        return SourceSeries(
            source_id=series_id,
            title=title.text().strip() if title else series.title if isinstance(series, SourceSeries) else series_id.rstrip("/").rsplit("/", 1)[-1],
            source_name=self.name,
            cover_url=_image_url(image, str(response.url)) if image else None,
            description=description.text().strip() if description else None,
            status=self._madara_status(status.text() if status else ""),
            content_tags=tuple(dict.fromkeys(genres)),
            metadata=series.metadata if isinstance(series, SourceSeries) else {},
            web_url=str(response.url),
        )

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        chapter_list = _first(root, lambda node: node.attrs.get("id") == "chapterlist")
        result = []
        for item in chapter_list.descendants("li") if chapter_list else []:
            link_box = _first(item, lambda node: node.has_class("dt"))
            anchor = _first(link_box, lambda node: node.tag == "a" and bool(node.attrs.get("href"))) if link_box else None
            if anchor is None:
                continue
            title = " ".join(
                node.text().strip() for node in item.descendants()
                if node.has_class("chapternum") and node.text().strip()
            ) or anchor.text().strip()
            number = re.search(r"\d+(?:\.\d+)?", title)
            date = _first(item, lambda node: node.has_class("chapterdate"))
            result.append(SourceChapter(
                source_id=urljoin(str(response.url), anchor.attrs["href"]),
                title=title or "Capítulo", series_id=series_id, source_name=self.name,
                number=float(number.group()) if number else None, language=self.language,
                uploaded_at=self._madara_date(date.text() if date else ""),
            ))
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else str(chapter)
        response = await self._request("GET", urljoin(f"{self.base_url}/", chapter_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        packed = next((script.text() for script in root.descendants("script") if "eval(" in script.text()), "")
        unpacked = self._unpack_packer(packed)
        found = re.search(r'''["']images["']\s*:\s*(\[.*?])''', unpacked, re.S)
        if found is None:
            return []
        try:
            encoded = json.loads(re.sub(r",\s*]", "]", found.group(1)))
        except json.JSONDecodeError:
            return []
        urls = []
        for value in encoded:
            try:
                decoded = base64.b64decode(str(value)).decode()
            except (ValueError, UnicodeDecodeError):
                continue
            urls.append(f"https:{decoded}")
        return self._source_pages(urls, chapter_id)

class GeneratedMangaThemesiaSource(MangaThemesiaSource):
    name = 'razure_en'
    display_name = 'Razure'
    base_url = 'https://razure.org'
    language = 'en'
    manga_directory = '/series'
    reader_id = 'readerarea'
    supports_latest = True
    requests_per_minute = 60
    image_no_referer_hosts = ()
    search_profile = 'default'
    browse_profile = 'default'
    chapter_profile = 'default'
    pages_profile = 'default'
    reader_class = ''
    image_class = ''
    page_element_classes = ()
    request_referer = ''
    accept_language = ''
    project_directory = ''
    date_format = 'MMMM dd, yyyy'
    date_locale = 'en'
    content_warning = 'safe'


SOURCE = GeneratedMangaThemesiaSource

"""Puente de contrato para adaptadores que conservan metodos v3."""

import inspect
from collections.abc import Mapping
from typing import Any

from nyanko_api.sources.contract import Paginated, SourceFilter, SourcePreference


def _arguments(method: Any, page: int, filters: Mapping[str, Any] | None) -> dict[str, Any]:
    parameters = inspect.signature(method).parameters
    arguments: dict[str, Any] = {}
    if "page" in parameters:
        arguments["page"] = page
    if "filters" in parameters:
        arguments["filters"] = filters
    if "limit" in parameters:
        arguments["limit"] = 20
    return arguments


def _paginated(value: Any, has_more: bool) -> Paginated:
    if isinstance(value, Paginated):
        return value
    if isinstance(value, dict):
        items = value.get("items", value.get("results", []))
        has_more = bool(value.get("has_more", value.get("has_next_page", has_more)))
    else:
        items = value or []
    return Paginated(items=list(items), has_more=has_more and bool(items))


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
    class SourceV4(legacy_source):
        async def get_filters(self) -> list[SourceFilter]:
            getter = getattr(super(), "get_filters", None)
            if not getter:
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
            arguments = _arguments(method, page, filters)
            return _paginated(
                await method(query, **arguments),
                "page" in inspect.signature(method).parameters,
            )

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
