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
    SourcePage,
    SourcePageContent,
    SourceFilter,
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
        "data-src",
        "data-lazy-src",
        "data-cfsrc",
        "data-manga-src",
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
                response = await self._request("POST", f"{series_url.rstrip('/')}/ajax/chapters")
            else:
                response = await self._request(
                    "POST",
                    f"{self.base_url}/wp-admin/admin-ajax.php",
                    data={"action": "manga_get_chapters", "manga": holder.attrs.get("data-id", "")},
                )
                if getattr(response, "status_code", 200) == 400:
                    response = await self._request("POST", f"{series_url.rstrip('/')}/ajax/chapters")
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
                )
            )
        return result

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
        if reading is not None:
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
        if isinstance(page, SourcePage):
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

    async def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        if self.fetcher is None:
            raise SourceNotFoundError(f"{self.display_name} no tiene fetcher inyectado")
        return await self.fetcher.request(method, url, **kwargs)

"""Fuente HTTP adaptable para extensiones sin un motor compartido."""

import json
import re
from urllib.parse import urljoin

try:
    from .madara import MadaraSource, SourceChapter, SourceFilter,
    SourcePreference,
    SourceSeries, _first, _image_url, _parse_html
except ImportError:
    pass


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

class GeneratedGenericSource(GenericSource):

    def get_preferences(self) -> list[SourcePreference]:
        # Autogenerated via heuristic port
        data = []
        return [SourcePreference(**item) for item in data]

    def get_filters(self) -> list[SourceFilter]:
        # Autogenerated via heuristic port
        data = [
                {
                                "type": "select",
                                "id": "generic_filter",
                                "name": "Filtro",
                                "options": [
                                                {
                                                                "name": "3D Porn Comics",
                                                                "value": "3d-porn-comics-xxx"
                                                },
                                                {
                                                                "name": "Adventure Time Porn",
                                                                "value": "adventure-time"
                                                },
                                                {
                                                                "name": "American Dad Porn",
                                                                "value": "american-dad-xx-porn-comix"
                                                },
                                                {
                                                                "name": "Attack on Titan Hentai",
                                                                "value": "attack-on-titan-hentai"
                                                },
                                                {
                                                                "name": "Ben 10 Porn Comics",
                                                                "value": "ben-10-porn-comics-v1"
                                                },
                                                {
                                                                "name": "Chainsaw Man Porn Comics",
                                                                "value": "porn-chainsaw-man-porn-comics"
                                                },
                                                {
                                                                "name": "Dragon Ball Porn",
                                                                "value": "dragon-ball-z-porn-comics-v1"
                                                },
                                                {
                                                                "name": "Exclusive",
                                                                "value": "exclusive"
                                                },
                                                {
                                                                "name": "Furry Porn Comics",
                                                                "value": "porn-comic-furry"
                                                },
                                                {
                                                                "name": "Hentai Manga",
                                                                "value": "xxxx-manga"
                                                },
                                                {
                                                                "name": "Interracial Porn",
                                                                "value": "interracial-porn-comix2-xxx"
                                                },
                                                {
                                                                "name": "Kim Possible Porn",
                                                                "value": "kim-possible-porn1"
                                                },
                                                {
                                                                "name": "LoL Hentai",
                                                                "value": "lol-hentai-xxx-comics1"
                                                },
                                                {
                                                                "name": "My Hero Academia Hentai",
                                                                "value": "hero-academia-porn-comic-v1"
                                                },
                                                {
                                                                "name": "Naruto Hentai",
                                                                "value": "naruto-hentai-comic4"
                                                },
                                                {
                                                                "name": "One Piece Hentai",
                                                                "value": "one-piece-hentai-v2"
                                                },
                                                {
                                                                "name": "Palcomix",
                                                                "value": "palcomix"
                                                },
                                                {
                                                                "name": "Pokemon Porn",
                                                                "value": "pokemon-porn-comics-v1"
                                                },
                                                {
                                                                "name": "Porn Comics",
                                                                "value": "porn-c0mics"
                                                },
                                                {
                                                                "name": "Princess Peach Porn",
                                                                "value": "princess-peach-porn-xxx"
                                                },
                                                {
                                                                "name": "Rick and Morty Porn Comics",
                                                                "value": "rick-and-morty-porn-comics"
                                                },
                                                {
                                                                "name": "Simpsons Porn",
                                                                "value": "simpsons-xxx-porn-comics"
                                                },
                                                {
                                                                "name": "Sonic Porn Comics",
                                                                "value": "sonic_porn-comics"
                                                },
                                                {
                                                                "name": "Sword Art Online Hentai",
                                                                "value": "sword-art-online-xxx-hentai1"
                                                },
                                                {
                                                                "name": "Teen Titans Porn",
                                                                "value": "teen-titans-porn-v1"
                                                },
                                                {
                                                                "name": "Aarokira",
                                                                "value": "aarokira"
                                                },
                                                {
                                                                "name": "Accel Art",
                                                                "value": "accel-art"
                                                },
                                                {
                                                                "name": "Aerith Hentai",
                                                                "value": "aerith-hentai"
                                                },
                                                {
                                                                "name": "Afrobull",
                                                                "value": "afrobull"
                                                },
                                                {
                                                                "name": "agent aika",
                                                                "value": "agent-aika"
                                                },
                                                {
                                                                "name": "Ahri",
                                                                "value": "ahri"
                                                },
                                                {
                                                                "name": "Ahri Hentai",
                                                                "value": "ahri-hentai"
                                                },
                                                {
                                                                "name": "Ahsoka",
                                                                "value": "ahsoka"
                                                },
                                                {
                                                                "name": "Akali Hentai",
                                                                "value": "akali-hentai1"
                                                },
                                                {
                                                                "name": "Alien Girl",
                                                                "value": "alien-girl"
                                                },
                                                {
                                                                "name": "American Dragon",
                                                                "value": "american-dragon"
                                                },
                                                {
                                                                "name": "Amethyst",
                                                                "value": "amethyst"
                                                },
                                                {
                                                                "name": "Amity Blight",
                                                                "value": "amity-blight"
                                                },
                                                {
                                                                "name": "Among Us",
                                                                "value": "among-us"
                                                },
                                                {
                                                                "name": "Amphibia Porn",
                                                                "value": "amphibia-porn"
                                                },
                                                {
                                                                "name": "Amy",
                                                                "value": "amy"
                                                },
                                                {
                                                                "name": "Amy Rose Hentai",
                                                                "value": "amy-rose-hentai"
                                                },
                                                {
                                                                "name": "Amy Wong",
                                                                "value": "amy-wong"
                                                },
                                                {
                                                                "name": "Anal",
                                                                "value": "anal"
                                                },
                                                {
                                                                "name": "Anall",
                                                                "value": "anall"
                                                },
                                                {
                                                                "name": "Android 18 Hentai",
                                                                "value": "android_18_xxx-hentai"
                                                },
                                                {
                                                                "name": "Android 21 Hentai",
                                                                "value": "android-21-hentai-xxx"
                                                },
                                                {
                                                                "name": "Animal Crossing",
                                                                "value": "animal-crossing-hentai-v1"
                                                },
                                                {
                                                                "name": "Animated",
                                                                "value": "animated"
                                                },
                                                {
                                                                "name": "Ankha",
                                                                "value": "ankha"
                                                },
                                                {
                                                                "name": "Ann Possible",
                                                                "value": "ann-possible"
                                                },
                                                {
                                                                "name": "Arabatos",
                                                                "value": "arabatos"
                                                },
                                                {
                                                                "name": "Area",
                                                                "value": "area"
                                                },
                                                {
                                                                "name": "Aroma Sensei",
                                                                "value": "aroma-sensei"
                                                },
                                                {
                                                                "name": "Ashe Hentai",
                                                                "value": "ashe-hentai"
                                                },
                                                {
                                                                "name": "Asuka Hentai",
                                                                "value": "asuka-henta1"
                                                },
                                                {
                                                                "name": "Asuna Hentai",
                                                                "value": "asuna-xxx-hentai"
                                                },
                                                {
                                                                "name": "Atlantis The Lost Empire",
                                                                "value": "atlantis-the-lost-empire"
                                                },
                                                {
                                                                "name": "Atomic Heart Porn",
                                                                "value": "atomic-heart-porn"
                                                },
                                                {
                                                                "name": "Avatar Hentai",
                                                                "value": "avatar-hentai-v1"
                                                },
                                                {
                                                                "name": "Avengers",
                                                                "value": "avengers"
                                                },
                                                {
                                                                "name": "Batgirl",
                                                                "value": "batgirl"
                                                },
                                                {
                                                                "name": "Batman Porn Comics",
                                                                "value": "batman-porn-comics-xxx"
                                                },
                                                {
                                                                "name": "Bayonetta",
                                                                "value": "bayonetta"
                                                },
                                                {
                                                                "name": "BDSM",
                                                                "value": "bdsm"
                                                },
                                                {
                                                                "name": "Beast Boy",
                                                                "value": "beast-boy"
                                                },
                                                {
                                                                "name": "Big As",
                                                                "value": "big-as"
                                                },
                                                {
                                                                "name": "Big Ass",
                                                                "value": "big-ass"
                                                },
                                                {
                                                                "name": "Big Black Cock",
                                                                "value": "big-black-cock"
                                                },
                                                {
                                                                "name": "Big Boobs",
                                                                "value": "big-boobs-xxx"
                                                },
                                                {
                                                                "name": "Big Breasts",
                                                                "value": "big-breasts-hentai"
                                                },
                                                {
                                                                "name": "Big Cock",
                                                                "value": "big-cock"
                                                },
                                                {
                                                                "name": "Big Dick",
                                                                "value": "big-dick"
                                                },
                                                {
                                                                "name": "Big Hero 6",
                                                                "value": "big-hero-6-porn"
                                                },
                                                {
                                                                "name": "Big Tits",
                                                                "value": "big-tit"
                                                },
                                                {
                                                                "name": "Bigdad",
                                                                "value": "bigdad"
                                                },
                                                {
                                                                "name": "Bioshock Porn",
                                                                "value": "bioshock-porn"
                                                },
                                                {
                                                                "name": "Black Clover Porn",
                                                                "value": "black-clover-porn-xxx"
                                                },
                                                {
                                                                "name": "blackfire",
                                                                "value": "blackfire"
                                                },
                                                {
                                                                "name": "Blacknwhite",
                                                                "value": "blacknwhite"
                                                },
                                                {
                                                                "name": "Blaze",
                                                                "value": "blaze"
                                                },
                                                {
                                                                "name": "Bleach Hentai",
                                                                "value": "bleach-porn-comics-xxx"
                                                },
                                                {
                                                                "name": "Bloodborne",
                                                                "value": "bloodborne"
                                                },
                                                {
                                                                "name": "Blowjob",
                                                                "value": "blowjob"
                                                },
                                                {
                                                                "name": "Blue Archive",
                                                                "value": "blue-archive"
                                                },
                                                {
                                                                "name": "Boa Hancock Hentai",
                                                                "value": "boa-hancock-hentai-xxx1"
                                                },
                                                {
                                                                "name": "Boruto",
                                                                "value": "boruto"
                                                },
                                                {
                                                                "name": "Botbot",
                                                                "value": "botbot"
                                                },
                                                {
                                                                "name": "Bowsette Hentai",
                                                                "value": "bowsette-hentai1"
                                                },
                                                {
                                                                "name": "Brain Dead 13 Porn",
                                                                "value": "brain-dead-13-porn"
                                                },
                                                {
                                                                "name": "Brandy and mr Whiskers",
                                                                "value": "brandy-and-mr-whiskers"
                                                },
                                                {
                                                                "name": "Briar Hentai",
                                                                "value": "briar-hentai"
                                                },
                                                {
                                                                "name": "Bulma Hentai",
                                                                "value": "bulma-h3ntaix"
                                                },
                                                {
                                                                "name": "Bunny Girl",
                                                                "value": "bunny-girl"
                                                },
                                                {
                                                                "name": "Carmelita",
                                                                "value": "carmelita"
                                                },
                                                {
                                                                "name": "carrot",
                                                                "value": "carrot"
                                                },
                                                {
                                                                "name": "Catgirl",
                                                                "value": "catgirl"
                                                },
                                                {
                                                                "name": "Catunder",
                                                                "value": "catunder"
                                                },
                                                {
                                                                "name": "Caulifla Hentai",
                                                                "value": "caulifla-hentai"
                                                },
                                                {
                                                                "name": "chainsaw man porn comic",
                                                                "value": "chainsaw-man-porn-comic"
                                                },
                                                {
                                                                "name": "Chara",
                                                                "value": "chara"
                                                },
                                                {
                                                                "name": "Cheelai Hentai",
                                                                "value": "cheelai-hentai"
                                                },
                                                {
                                                                "name": "Cherry Road Porn Comics",
                                                                "value": "cherry-road-porn-comics"
                                                },
                                                {
                                                                "name": "Chi-Chi Hentai",
                                                                "value": "chi-chi_hentai"
                                                },
                                                {
                                                                "name": "Chloe",
                                                                "value": "chloe"
                                                },
                                                {
                                                                "name": "ChoChoX",
                                                                "value": "chochox"
                                                },
                                                {
                                                                "name": "Chun-Li",
                                                                "value": "chun-li"
                                                },
                                                {
                                                                "name": "Coco Bandicoot Hentai",
                                                                "value": "coco-bandicoot-hentai"
                                                },
                                                {
                                                                "name": "Code Lyoko",
                                                                "value": "code-lyoko"
                                                },
                                                {
                                                                "name": "Connie Maheswaran",
                                                                "value": "connie-maheswaran"
                                                },
                                                {
                                                                "name": "Cookie Run Porn",
                                                                "value": "cookie-run-porn"
                                                },
                                                {
                                                                "name": "Cortana Porn",
                                                                "value": "cortana-porn"
                                                },
                                                {
                                                                "name": "Crash Bandicoot",
                                                                "value": "crash-bandicoot-porn"
                                                },
                                                {
                                                                "name": "CrazyDad3D",
                                                                "value": "crazydad3d"
                                                },
                                                {
                                                                "name": "Cream the Rabbit",
                                                                "value": "cream-the-rabbit"
                                                },
                                                {
                                                                "name": "CrockComix",
                                                                "value": "crockcomix"
                                                },
                                                {
                                                                "name": "Crossdressing",
                                                                "value": "crossdressing"
                                                },
                                                {
                                                                "name": "Cumshot",
                                                                "value": "cumshot"
                                                },
                                                {
                                                                "name": "Cunnilingus",
                                                                "value": "cunnilingus"
                                                },
                                                {
                                                                "name": "Cuphead Hentai",
                                                                "value": "cuphead-hentai"
                                                },
                                                {
                                                                "name": "Cyberpunk Edgerunners Porn",
                                                                "value": "cyberpunk-edgerunners-p0rn-comics"
                                                },
                                                {
                                                                "name": "Daisy Hentai",
                                                                "value": "daisy-hentai"
                                                },
                                                {
                                                                "name": "Dandadan Hentai",
                                                                "value": "dandadan-comic2-hentai"
                                                },
                                                {
                                                                "name": "Danganronpa",
                                                                "value": "danganronpa"
                                                },
                                                {
                                                                "name": "Danny Phantom Porn",
                                                                "value": "danny-phantom_porn-comics"
                                                },
                                                {
                                                                "name": "Daphne Hentai",
                                                                "value": "daphne-hentai"
                                                },
                                                {
                                                                "name": "Dark Souls",
                                                                "value": "dark-souls"
                                                },
                                                {
                                                                "name": "Darkstalkers Porn",
                                                                "value": "darkstalkers-porn"
                                                },
                                                {
                                                                "name": "Dawn Hentai",
                                                                "value": "dawn-hentai"
                                                },
                                                {
                                                                "name": "DC Porn",
                                                                "value": "dc-porn"
                                                },
                                                {
                                                                "name": "Deadpool",
                                                                "value": "deadpool"
                                                },
                                                {
                                                                "name": "Dee Dee",
                                                                "value": "dee-dee"
                                                },
                                                {
                                                                "name": "Deepthroat",
                                                                "value": "deepthroat"
                                                },
                                                {
                                                                "name": "Deku",
                                                                "value": "deku"
                                                },
                                                {
                                                                "name": "Deltarune Porn",
                                                                "value": "deltarune-porn"
                                                },
                                                {
                                                                "name": "Demon Girl",
                                                                "value": "demon-girl"
                                                },
                                                {
                                                                "name": "Demon Slayer Nezuko Porn",
                                                                "value": "demon-slayer-nezuko-porn1"
                                                },
                                                {
                                                                "name": "Demon Slayer Porn",
                                                                "value": "demon-slayer-porn-comic1-xxx"
                                                },
                                                {
                                                                "name": "Devil May Cry",
                                                                "value": "devil-may-cry"
                                                },
                                                {
                                                                "name": "Devvil May Cry Porn",
                                                                "value": "devvil-may-cry-porn"
                                                },
                                                {
                                                                "name": "Dexter Mom Hentai",
                                                                "value": "dexter-mom-hentai1"
                                                },
                                                {
                                                                "name": "Dexter Porn Comics",
                                                                "value": "dexter-porn-comics"
                                                },
                                                {
                                                                "name": "Diane",
                                                                "value": "diane"
                                                },
                                                {
                                                                "name": "Digimon XXX",
                                                                "value": "digimon-xxx"
                                                },
                                                {
                                                                "name": "Dipper",
                                                                "value": "dipper"
                                                },
                                                {
                                                                "name": "Diva Hentai",
                                                                "value": "diva-hentai"
                                                },
                                                {
                                                                "name": "Doom",
                                                                "value": "doom"
                                                },
                                                {
                                                                "name": "Dora",
                                                                "value": "dora"
                                                },
                                                {
                                                                "name": "Double Anal",
                                                                "value": "double-anal"
                                                },
                                                {
                                                                "name": "Double Penetration",
                                                                "value": "double-penetration"
                                                },
                                                {
                                                                "name": "Dr. Stone",
                                                                "value": "dr-stone-x"
                                                },
                                                {
                                                                "name": "Dragon Quest Porn",
                                                                "value": "dragon-quest-porn"
                                                },
                                                {
                                                                "name": "Drah Navlag",
                                                                "value": "drah-navlag"
                                                },
                                                {
                                                                "name": "Dsan",
                                                                "value": "dsan"
                                                },
                                                {
                                                                "name": "dungeon and dragons",
                                                                "value": "dungeon-and-dragons"
                                                },
                                                {
                                                                "name": "Ed Edd n Eddy Porn",
                                                                "value": "ed-edd-n-eddy-porn"
                                                },
                                                {
                                                                "name": "Elden Ring",
                                                                "value": "elden-ring"
                                                },
                                                {
                                                                "name": "Elf",
                                                                "value": "elf"
                                                },
                                                {
                                                                "name": "Elizabeth Liones porn",
                                                                "value": "elizabeth-liones-porn"
                                                },
                                                {
                                                                "name": "Elsa",
                                                                "value": "elsa-hentai"
                                                },
                                                {
                                                                "name": "Emma Frost",
                                                                "value": "emma-frost"
                                                },
                                                {
                                                                "name": "Epic Seven Hentai",
                                                                "value": "epic-seven-hentai"
                                                },
                                                {
                                                                "name": "Ero Mantic",
                                                                "value": "ero-mantic"
                                                },
                                                {
                                                                "name": "Evangelion Hentai",
                                                                "value": "henta1-evangeli0n"
                                                },
                                                {
                                                                "name": "Evee",
                                                                "value": "evee"
                                                },
                                                {
                                                                "name": "Evelynn",
                                                                "value": "evelynn"
                                                },
                                                {
                                                                "name": "Fairy Tail",
                                                                "value": "fairy-tail-hentai-v0"
                                                },
                                                {
                                                                "name": "Fallout XXX",
                                                                "value": "fallout-xxx-hentai"
                                                },
                                                {
                                                                "name": "Family Guy Porn Comics",
                                                                "value": "family-guy-porn_comix"
                                                },
                                                {
                                                                "name": "Fate Grand Order Hentai",
                                                                "value": "fate-grand-order-hentai-v0"
                                                },
                                                {
                                                                "name": "Feith Noir",
                                                                "value": "feith-noir"
                                                },
                                                {
                                                                "name": "Felsala",
                                                                "value": "felsala"
                                                },
                                                {
                                                                "name": "Final Fantasy Hentai",
                                                                "value": "final-fantasy-hentai-v2"
                                                },
                                                {
                                                                "name": "Finn",
                                                                "value": "finn"
                                                },
                                                {
                                                                "name": "Fiona",
                                                                "value": "fiona"
                                                },
                                                {
                                                                "name": "Fiora",
                                                                "value": "fiora"
                                                },
                                                {
                                                                "name": "Fire Emblem",
                                                                "value": "fire-emblem"
                                                },
                                                {
                                                                "name": "five nights at freddy's Porn",
                                                                "value": "five-nights-at-freddys-porn"
                                                },
                                                {
                                                                "name": "Flame Princess",
                                                                "value": "flame-princess"
                                                },
                                                {
                                                                "name": "Footjob",
                                                                "value": "footjob"
                                                },
                                                {
                                                                "name": "Fortnite",
                                                                "value": "fortnite"
                                                },
                                                {
                                                                "name": "Foster’s Home for Imaginary Friends Porn",
                                                                "value": "fosters-home-for-imaginary-friends-porn"
                                                },
                                                {
                                                                "name": "Francine Porn Comics",
                                                                "value": "francine-porn-comics"
                                                },
                                                {
                                                                "name": "Frankie Foster Hentai",
                                                                "value": "frankie-foster-hentai"
                                                },
                                                {
                                                                "name": "Fred Perry",
                                                                "value": "fred-perry"
                                                },
                                                {
                                                                "name": "Frieren Porn",
                                                                "value": "frieren-porn"
                                                },
                                                {
                                                                "name": "Frozen",
                                                                "value": "frozen"
                                                },
                                                {
                                                                "name": "Fubuki Hentai",
                                                                "value": "fubuki-hentai"
                                                },
                                                {
                                                                "name": "Full Color",
                                                                "value": "full-color-comic"
                                                },
                                                {
                                                                "name": "FunsexyDB",
                                                                "value": "funsexydb"
                                                },
                                                {
                                                                "name": "Furret El Furro",
                                                                "value": "furret-el-furro"
                                                },
                                                {
                                                                "name": "Futa Comic",
                                                                "value": "futanari-comic-xxx"
                                                },
                                                {
                                                                "name": "Futurama Porn Comics",
                                                                "value": "futurama_porn-c0mics1"
                                                },
                                                {
                                                                "name": "Gansoman",
                                                                "value": "gansoman"
                                                },
                                                {
                                                                "name": "Gardevoir Hentai",
                                                                "value": "gardevoir-hentaix"
                                                },
                                                {
                                                                "name": "Garnet Hentai",
                                                                "value": "garnet-hentai"
                                                },
                                                {
                                                                "name": "gay porn comics",
                                                                "value": "porn-comics-gay"
                                                },
                                                {
                                                                "name": "Gemma Hentai",
                                                                "value": "gemma-hentai"
                                                },
                                                {
                                                                "name": "Gender Bender",
                                                                "value": "gender-bender"
                                                },
                                                {
                                                                "name": "Genshin Impact Porn",
                                                                "value": "genshin-impact-p0rnx"
                                                },
                                                {
                                                                "name": "Gine Hentai",
                                                                "value": "gine-hentai"
                                                },
                                                {
                                                                "name": "Girls und Panzer Hentai",
                                                                "value": "girls-und-panzer_hentai"
                                                },
                                                {
                                                                "name": "Girls' Frontline Hentai",
                                                                "value": "girls-frontline-hentai"
                                                },
                                                {
                                                                "name": "Glitch Techs",
                                                                "value": "glitch-techs"
                                                },
                                                {
                                                                "name": "Glory Hole",
                                                                "value": "glory-hole"
                                                },
                                                {
                                                                "name": "Goblin",
                                                                "value": "goblin"
                                                },
                                                {
                                                                "name": "Goku",
                                                                "value": "goku"
                                                },
                                                {
                                                                "name": "gold digger",
                                                                "value": "gold-digger"
                                                },
                                                {
                                                                "name": "Granblue Fantasy",
                                                                "value": "granblue-fantasy"
                                                },
                                                {
                                                                "name": "Gravity Falls Porn",
                                                                "value": "gravity-falls-porn-comics"
                                                },
                                                {
                                                                "name": "Great Fairy Hentai",
                                                                "value": "great-fairy-hentai"
                                                },
                                                {
                                                                "name": "Greendogg",
                                                                "value": "greendogg"
                                                },
                                                {
                                                                "name": "Guilty Gear",
                                                                "value": "guilty-gear"
                                                },
                                                {
                                                                "name": "Gumball",
                                                                "value": "gumball"
                                                },
                                                {
                                                                "name": "Gwen",
                                                                "value": "gwen"
                                                },
                                                {
                                                                "name": "Gwen Stacy",
                                                                "value": "gwen-stacy"
                                                },
                                                {
                                                                "name": "GygerBeen",
                                                                "value": "gygerbeen"
                                                },
                                                {
                                                                "name": "Hagfish",
                                                                "value": "hagfish"
                                                },
                                                {
                                                                "name": "Haikon Knight",
                                                                "value": "haikon-knight"
                                                },
                                                {
                                                                "name": "Halo",
                                                                "value": "halo"
                                                },
                                                {
                                                                "name": "Handjob",
                                                                "value": "handjob"
                                                },
                                                {
                                                                "name": "Harley Quinn",
                                                                "value": "harley-quinn"
                                                },
                                                {
                                                                "name": "Harry Potter",
                                                                "value": "harry-potter"
                                                },
                                                {
                                                                "name": "Hatsune Miku Hentai",
                                                                "value": "hatsune-miku-hentai"
                                                },
                                                {
                                                                "name": "Hazbin Hotel",
                                                                "value": "hazbin-hotel-pornx"
                                                },
                                                {
                                                                "name": "Hekapoo",
                                                                "value": "hekapoo-hentai"
                                                },
                                                {
                                                                "name": "Helluva Boss Porn",
                                                                "value": "helluva-boss-comics-xxx"
                                                },
                                                {
                                                                "name": "Hermione Granger",
                                                                "value": "hermione-granger"
                                                },
                                                {
                                                                "name": "Hermit Moth Hentai",
                                                                "value": "hermit-moth-hentai"
                                                },
                                                {
                                                                "name": "HermitMoth",
                                                                "value": "hermitmoth"
                                                },
                                                {
                                                                "name": "Hey Arnold",
                                                                "value": "hey-arnold"
                                                },
                                                {
                                                                "name": "Highschool DxD",
                                                                "value": "highschool-dxd"
                                                },
                                                {
                                                                "name": "Hilda",
                                                                "value": "hilda"
                                                },
                                                {
                                                                "name": "Hilda Porn",
                                                                "value": "hilda-porn"
                                                },
                                                {
                                                                "name": "Himiko Toga Hentai",
                                                                "value": "himiko-toga-hentai"
                                                },
                                                {
                                                                "name": "Hinata Hentai",
                                                                "value": "hinata-hentai-xxx2"
                                                },
                                                {
                                                                "name": "Honey Lemon Hentai",
                                                                "value": "honey-lemon-hentai"
                                                },
                                                {
                                                                "name": "Hornyx",
                                                                "value": "hornyx"
                                                },
                                                {
                                                                "name": "Hot Step Sister",
                                                                "value": "hot-step-sister"
                                                },
                                                {
                                                                "name": "Hotel Transylvania Porn Comics",
                                                                "value": "hotel_transylvania-porn-comics"
                                                },
                                                {
                                                                "name": "Huge Breasts",
                                                                "value": "huge-breasts"
                                                },
                                                {
                                                                "name": "Ice Queen Hentai",
                                                                "value": "ice-queen-hentai"
                                                },
                                                {
                                                                "name": "Impa Hentai",
                                                                "value": "impa-hentai"
                                                },
                                                {
                                                                "name": "Incestibles",
                                                                "value": "incestibles"
                                                },
                                                {
                                                                "name": "Incognitymous",
                                                                "value": "incognitymous-comic"
                                                },
                                                {
                                                                "name": "Ino",
                                                                "value": "ino"
                                                },
                                                {
                                                                "name": "Ino Hentai",
                                                                "value": "ino-hentai-xxx"
                                                },
                                                {
                                                                "name": "Inside Out",
                                                                "value": "inside-out"
                                                },
                                                {
                                                                "name": "Inspector Gadget",
                                                                "value": "inspector-gadget"
                                                },
                                                {
                                                                "name": "Inuyuru",
                                                                "value": "inuyuru"
                                                },
                                                {
                                                                "name": "Invader Zim Porn",
                                                                "value": "invader-zim-porn"
                                                },
                                                {
                                                                "name": "INVINCIBLE Porn",
                                                                "value": "invincible-porn"
                                                },
                                                {
                                                                "name": "Irelia Hentai",
                                                                "value": "irelia-hentai"
                                                },
                                                {
                                                                "name": "IS Infinite Stratos",
                                                                "value": "is-infinite-stratos"
                                                },
                                                {
                                                                "name": "Isabelle",
                                                                "value": "isabelle"
                                                },
                                                {
                                                                "name": "Itsuka Kendo Hentai",
                                                                "value": "itsuka-kendo-hentai"
                                                },
                                                {
                                                                "name": "Jack and Daxter",
                                                                "value": "jack-and-daxter"
                                                },
                                                {
                                                                "name": "Jackie Lynn",
                                                                "value": "jackie-lynn"
                                                },
                                                {
                                                                "name": "Jaguar",
                                                                "value": "jaguar"
                                                },
                                                {
                                                                "name": "Jaiden XXX",
                                                                "value": "jaiden-xxx"
                                                },
                                                {
                                                                "name": "Janna",
                                                                "value": "janna"
                                                },
                                                {
                                                                "name": "Jasmine",
                                                                "value": "jasmine"
                                                },
                                                {
                                                                "name": "JDseal",
                                                                "value": "jdseal"
                                                },
                                                {
                                                                "name": "Jenny Hentai",
                                                                "value": "jenny-hentai"
                                                },
                                                {
                                                                "name": "Jessica Rabbit",
                                                                "value": "jessica-rabbit"
                                                },
                                                {
                                                                "name": "Jessie Porn",
                                                                "value": "jessie-porn"
                                                },
                                                {
                                                                "name": "Jill Valentine",
                                                                "value": "jill-valentine"
                                                },
                                                {
                                                                "name": "Jimmy Neutron",
                                                                "value": "jimmy-neutron"
                                                },
                                                {
                                                                "name": "Jinx",
                                                                "value": "jinx"
                                                },
                                                {
                                                                "name": "Jinx Hentai",
                                                                "value": "jinx-hentai-xxx-v2"
                                                },
                                                {
                                                                "name": "Jirou Hentai",
                                                                "value": "jirou_hentai"
                                                },
                                                {
                                                                "name": "Jlullaby",
                                                                "value": "jlullaby"
                                                },
                                                {
                                                                "name": "Johnny Test",
                                                                "value": "johnny-test"
                                                },
                                                {
                                                                "name": "Jujutsu Kaisen",
                                                                "value": "jujutsu-kaisen"
                                                },
                                                {
                                                                "name": "Justice League",
                                                                "value": "justice-league"
                                                },
                                                {
                                                                "name": "JZerosk",
                                                                "value": "jzerosk"
                                                },
                                                {
                                                                "name": "Kairi",
                                                                "value": "kairi"
                                                },
                                                {
                                                                "name": "Kale Hentai",
                                                                "value": "kale-hentai-xxx"
                                                },
                                                {
                                                                "name": "Kantai Collection Hentai",
                                                                "value": "kantai-collection-hentai"
                                                },
                                                {
                                                                "name": "Katarina Hentai",
                                                                "value": "katarina-hentai"
                                                },
                                                {
                                                                "name": "Kefla Hentai",
                                                                "value": "kefla-hentai"
                                                },
                                                {
                                                                "name": "Kid Icarus",
                                                                "value": "kid-icarus"
                                                },
                                                {
                                                                "name": "Kill la Kill",
                                                                "value": "kill-la-kill-hentai"
                                                },
                                                {
                                                                "name": "Kim Possible",
                                                                "value": "kim-possible-porn1"
                                                },
                                                {
                                                                "name": "kingdom hearts",
                                                                "value": "kingdom-hearts"
                                                },
                                                {
                                                                "name": "Kinkymation",
                                                                "value": "kinkymation-hentai"
                                                },
                                                {
                                                                "name": "Kirlia Hentai",
                                                                "value": "kirlia-hentai"
                                                },
                                                {
                                                                "name": "Kiryuin Satsuki Hentai",
                                                                "value": "kiryuin-satsuki-hentai"
                                                },
                                                {
                                                                "name": "Kogeikun",
                                                                "value": "kogeikun"
                                                },
                                                {
                                                                "name": "Lady Dimitrescu Porn",
                                                                "value": "lady_dimitrescu-porn"
                                                },
                                                {
                                                                "name": "Lady Dmittrescu",
                                                                "value": "lady-dmittrescu"
                                                },
                                                {
                                                                "name": "Lapis Lazuli",
                                                                "value": "lapis-lazuli"
                                                },
                                                {
                                                                "name": "Lara Croft",
                                                                "value": "lara-croft"
                                                },
                                                {
                                                                "name": "Leela Porn",
                                                                "value": "leela-porn"
                                                },
                                                {
                                                                "name": "Leona",
                                                                "value": "leona"
                                                },
                                                {
                                                                "name": "Lesbians",
                                                                "value": "lesbians"
                                                },
                                                {
                                                                "name": "Lilo and Stitch",
                                                                "value": "lilo-and-stitch"
                                                },
                                                {
                                                                "name": "Lina Inverse",
                                                                "value": "lina-inverse"
                                                },
                                                {
                                                                "name": "Lisa Simpson Porn",
                                                                "value": "lisa-simpson-porn"
                                                },
                                                {
                                                                "name": "Lissandra",
                                                                "value": "lissandra"
                                                },
                                                {
                                                                "name": "Little Nightmares",
                                                                "value": "little-nightmares"
                                                },
                                                {
                                                                "name": "Little Nightmares Porn",
                                                                "value": "little-nightmares-porn"
                                                },
                                                {
                                                                "name": "Little Witch Academia",
                                                                "value": "little-witch-academia"
                                                },
                                                {
                                                                "name": "Lola Bunny",
                                                                "value": "lola-bunny"
                                                },
                                                {
                                                                "name": "Looney Tunes Porn",
                                                                "value": "looney-tunes-porn"
                                                },
                                                {
                                                                "name": "Lori Loud Porn",
                                                                "value": "lori-loud-porn"
                                                },
                                                {
                                                                "name": "Luna Loud",
                                                                "value": "luna-loud"
                                                },
                                                {
                                                                "name": "Lux",
                                                                "value": "lux"
                                                },
                                                {
                                                                "name": "Luz Noceda",
                                                                "value": "luz-noceda"
                                                },
                                                {
                                                                "name": "Mabel",
                                                                "value": "mabel"
                                                },
                                                {
                                                                "name": "Macergo",
                                                                "value": "macergo"
                                                },
                                                {
                                                                "name": "Madeline Fenton",
                                                                "value": "madeline-fenton"
                                                },
                                                {
                                                                "name": "Mai Hentai",
                                                                "value": "mai-hentai"
                                                },
                                                {
                                                                "name": "Malefica",
                                                                "value": "malefica"
                                                },
                                                {
                                                                "name": "Maleficient",
                                                                "value": "maleficient"
                                                },
                                                {
                                                                "name": "Mana World",
                                                                "value": "mana-world"
                                                },
                                                {
                                                                "name": "Manaworld",
                                                                "value": "manaworld"
                                                },
                                                {
                                                                "name": "Marceline",
                                                                "value": "marceline"
                                                },
                                                {
                                                                "name": "Marco Diaz",
                                                                "value": "marco-diaz"
                                                },
                                                {
                                                                "name": "Marge Simpson Porn",
                                                                "value": "marge-simpson-porn-xxx"
                                                },
                                                {
                                                                "name": "Mario Bros",
                                                                "value": "mario-bros"
                                                },
                                                {
                                                                "name": "Marvel",
                                                                "value": "marvel"
                                                },
                                                {
                                                                "name": "Marvel Rivals Porn",
                                                                "value": "marvel-rivals-porn"
                                                },
                                                {
                                                                "name": "Mass Effect Porn",
                                                                "value": "mass-effect-porn"
                                                },
                                                {
                                                                "name": "Masturbation",
                                                                "value": "masturbation"
                                                },
                                                {
                                                                "name": "May Hentai",
                                                                "value": "may-hentai-xxx"
                                                },
                                                {
                                                                "name": "Meego",
                                                                "value": "meego"
                                                },
                                                {
                                                                "name": "Meg Griffin",
                                                                "value": "meg-griffin"
                                                },
                                                {
                                                                "name": "Mega Man",
                                                                "value": "mega-man"
                                                },
                                                {
                                                                "name": "Mei Hatsume Hentai",
                                                                "value": "mei-hatsume-hentai"
                                                },
                                                {
                                                                "name": "Mei Hentai",
                                                                "value": "mei-hentai"
                                                },
                                                {
                                                                "name": "Melkor Mancin",
                                                                "value": "melkor-mancin"
                                                },
                                                {
                                                                "name": "MelkorMancin",
                                                                "value": "melkormancin"
                                                },
                                                {
                                                                "name": "Mercy Hentai",
                                                                "value": "mercy-hentai"
                                                },
                                                {
                                                                "name": "Metroid",
                                                                "value": "metroid"
                                                },
                                                {
                                                                "name": "Midna Hentai",
                                                                "value": "midna-hentai"
                                                },
                                                {
                                                                "name": "Mif",
                                                                "value": "mif"
                                                },
                                                {
                                                                "name": "Mifl",
                                                                "value": "mifl"
                                                },
                                                {
                                                                "name": "Mikasa Hentai",
                                                                "value": "mikasa-henta1"
                                                },
                                                {
                                                                "name": "Miko",
                                                                "value": "miko"
                                                },
                                                {
                                                                "name": "Miles",
                                                                "value": "miles"
                                                },
                                                {
                                                                "name": "Miles Morales Porn Comics",
                                                                "value": "miles-morales-porn-comics"
                                                },
                                                {
                                                                "name": "Milf",
                                                                "value": "milf-xxx"
                                                },
                                                {
                                                                "name": "Milfs",
                                                                "value": "milfs"
                                                },
                                                {
                                                                "name": "Milk",
                                                                "value": "milk"
                                                },
                                                {
                                                                "name": "Milky Bunny",
                                                                "value": "milky-bunny"
                                                },
                                                {
                                                                "name": "Millie",
                                                                "value": "millie"
                                                },
                                                {
                                                                "name": "Milo Murphy's Law Porn",
                                                                "value": "milo-murphys-law-porn"
                                                },
                                                {
                                                                "name": "Mina Ashido Hentai",
                                                                "value": "mina-ashido-hentai1"
                                                },
                                                {
                                                                "name": "Minako Aino",
                                                                "value": "minako-aino"
                                                },
                                                {
                                                                "name": "Minecraft Porn",
                                                                "value": "minecraft-porn"
                                                },
                                                {
                                                                "name": "Mineta",
                                                                "value": "mineta"
                                                },
                                                {
                                                                "name": "Miraculous Ladybug",
                                                                "value": "miraculous-ladybug"
                                                },
                                                {
                                                                "name": "Misato Katsuragi Hentai",
                                                                "value": "misato-katsuragi-hentai"
                                                },
                                                {
                                                                "name": "Miss Heed",
                                                                "value": "miss-heed"
                                                },
                                                {
                                                                "name": "Misty Hentai",
                                                                "value": "misty-hentai"
                                                },
                                                {
                                                                "name": "Mitsuki Hentai",
                                                                "value": "mitsuki-hentai"
                                                },
                                                {
                                                                "name": "Mob Psycho 100",
                                                                "value": "mob-psycho-100"
                                                },
                                                {
                                                                "name": "Mobile Suit Gundam",
                                                                "value": "mobile-suit-gundam"
                                                },
                                                {
                                                                "name": "Momo Hentai",
                                                                "value": "momo_hentai"
                                                },
                                                {
                                                                "name": "Mona",
                                                                "value": "mona"
                                                },
                                                {
                                                                "name": "Monster Girl",
                                                                "value": "monster-girl"
                                                },
                                                {
                                                                "name": "monster hunter",
                                                                "value": "monster-hunter"
                                                },
                                                {
                                                                "name": "Morgana Hentai",
                                                                "value": "morgana-hentai"
                                                },
                                                {
                                                                "name": "Mossy Froot",
                                                                "value": "mossy-froot"
                                                },
                                                {
                                                                "name": "Mr. Jean Gobax",
                                                                "value": "mr-jean-gobax"
                                                },
                                                {
                                                                "name": "Mr.E",
                                                                "value": "mr-e"
                                                },
                                                {
                                                                "name": "My Bad Bunny",
                                                                "value": "my-bad-bunny"
                                                },
                                                {
                                                                "name": "My LIfe as a Teenage Robot",
                                                                "value": "my-life-as-a-teenage-robot"
                                                },
                                                {
                                                                "name": "My Little Pony Porn Comics",
                                                                "value": "porn-comic-xxx-my-little-pony1"
                                                },
                                                {
                                                                "name": "Nami Hentai",
                                                                "value": "nami-xxx-hentai-1"
                                                },
                                                {
                                                                "name": "Nana Shimura Hentai",
                                                                "value": "nana-shimura-hentai"
                                                },
                                                {
                                                                "name": "Neeko",
                                                                "value": "neeko"
                                                },
                                                {
                                                                "name": "Nejire Hado",
                                                                "value": "nejire-hado"
                                                },
                                                {
                                                                "name": "Nemona Hentai",
                                                                "value": "nemona-hentai"
                                                },
                                                {
                                                                "name": "Nemuri Kayama Hentai",
                                                                "value": "nemuri-kayama-hentai"
                                                },
                                                {
                                                                "name": "NGTVisualStudio",
                                                                "value": "ngtvisualstudio"
                                                },
                                                {
                                                                "name": "Nico Robin Hentai",
                                                                "value": "nico-robin-xxx-hentai2"
                                                },
                                                {
                                                                "name": "Nicole Watterson",
                                                                "value": "nicole-watterson"
                                                },
                                                {
                                                                "name": "Nier Automata",
                                                                "value": "nier-automata"
                                                },
                                                {
                                                                "name": "Nisego",
                                                                "value": "nisego"
                                                },
                                                {
                                                                "name": "Oban Star Racers",
                                                                "value": "oban-star-racers"
                                                },
                                                {
                                                                "name": "Ochako Uraraka",
                                                                "value": "ochako-uraraka"
                                                },
                                                {
                                                                "name": "OK K.O.! Let's Be Heroes Porn",
                                                                "value": "ok-k-o-lets-be-heroes-porn"
                                                },
                                                {
                                                                "name": "One Punch Man",
                                                                "value": "comic-hentai-one-punch-man-porn"
                                                },
                                                {
                                                                "name": "OnGoing",
                                                                "value": "ongoing"
                                                },
                                                {
                                                                "name": "Ousama Ranking",
                                                                "value": "ousama-ranking"
                                                },
                                                {
                                                                "name": "Overwatch Porn",
                                                                "value": "overwatch-porn"
                                                },
                                                {
                                                                "name": "Pacifica Porn",
                                                                "value": "pacifica-porn"
                                                },
                                                {
                                                                "name": "Palutena Hentai",
                                                                "value": "palutena-hentai"
                                                },
                                                {
                                                                "name": "Panchy",
                                                                "value": "panchy"
                                                },
                                                {
                                                                "name": "Paya Hentai",
                                                                "value": "paya-hentai"
                                                },
                                                {
                                                                "name": "Peach Hentai",
                                                                "value": "peach-hentai"
                                                },
                                                {
                                                                "name": "Pearl",
                                                                "value": "pearl"
                                                },
                                                {
                                                                "name": "Pedverse",
                                                                "value": "pedverse"
                                                },
                                                {
                                                                "name": "Peg",
                                                                "value": "peg"
                                                },
                                                {
                                                                "name": "Persona 2",
                                                                "value": "persona-2"
                                                },
                                                {
                                                                "name": "Persona 3",
                                                                "value": "persona-3"
                                                },
                                                {
                                                                "name": "Persona 4",
                                                                "value": "persona-4-xxx"
                                                },
                                                {
                                                                "name": "persona 5",
                                                                "value": "persona-5"
                                                },
                                                {
                                                                "name": "Phineas and Ferb",
                                                                "value": "phineas-and-ferb"
                                                },
                                                {
                                                                "name": "Pink Pawg",
                                                                "value": "pink-pawg"
                                                },
                                                {
                                                                "name": "Pokemon Scarlet",
                                                                "value": "pokemon-scarlet"
                                                },
                                                {
                                                                "name": "PokuArts",
                                                                "value": "pokuarts"
                                                },
                                                {
                                                                "name": "Pony Tsunotori Hentai",
                                                                "value": "pony-tsunotori-hentai"
                                                },
                                                {
                                                                "name": "Poppy",
                                                                "value": "poppy"
                                                },
                                                {
                                                                "name": "Porn Parody",
                                                                "value": "porn-parody"
                                                },
                                                {
                                                                "name": "Princess Bubblegum",
                                                                "value": "princess-bubblegum"
                                                },
                                                {
                                                                "name": "Princess Daisy",
                                                                "value": "princess-daisy"
                                                },
                                                {
                                                                "name": "Princess Peach",
                                                                "value": "princess-peach"
                                                },
                                                {
                                                                "name": "Prison School Hentai",
                                                                "value": "prison-school-hentai"
                                                },
                                                {
                                                                "name": "Priyanka Hentai",
                                                                "value": "priyanka-hentai"
                                                },
                                                {
                                                                "name": "Promare",
                                                                "value": "promare"
                                                },
                                                {
                                                                "name": "Purah Hentai",
                                                                "value": "purah-hentai"
                                                },
                                                {
                                                                "name": "Rainbow Mika",
                                                                "value": "rainbow-mika"
                                                },
                                                {
                                                                "name": "Rainbow Six Siege Porn",
                                                                "value": "rainbow-six-siege-porn"
                                                },
                                                {
                                                                "name": "Ranma Porn",
                                                                "value": "ranma-porn-comic"
                                                },
                                                {
                                                                "name": "Ratchet and Clank Porn",
                                                                "value": "ratchet-and-clank-porn"
                                                },
                                                {
                                                                "name": "Raven",
                                                                "value": "raven"
                                                },
                                                {
                                                                "name": "Raven xxx",
                                                                "value": "raven-hentaaii"
                                                },
                                                {
                                                                "name": "Razter",
                                                                "value": "razter"
                                                },
                                                {
                                                                "name": "Rebecca Cyberpunk Porn",
                                                                "value": "rebecca-cyberpunk-p0rn"
                                                },
                                                {
                                                                "name": "Rei Hentai",
                                                                "value": "rei-hentai"
                                                },
                                                {
                                                                "name": "RelatedGuy",
                                                                "value": "relatedguy-porn"
                                                },
                                                {
                                                                "name": "Rem Hentai",
                                                                "value": "rem-hentai"
                                                },
                                                {
                                                                "name": "Renamon",
                                                                "value": "renamon"
                                                },
                                                {
                                                                "name": "Resident Evil",
                                                                "value": "resident-evil-porn"
                                                },
                                                {
                                                                "name": "Rezero Hentai",
                                                                "value": "rezero-hentai"
                                                },
                                                {
                                                                "name": "Riukykappa",
                                                                "value": "riukykappa"
                                                },
                                                {
                                                                "name": "Riven",
                                                                "value": "riven"
                                                },
                                                {
                                                                "name": "Rivet Hentai",
                                                                "value": "rivet-hentai"
                                                },
                                                {
                                                                "name": "road to el dorado",
                                                                "value": "road-to-el-dorado"
                                                },
                                                {
                                                                "name": "Rosalina Hentai",
                                                                "value": "rosalina-hentai"
                                                },
                                                {
                                                                "name": "Rouge",
                                                                "value": "rouge"
                                                },
                                                {
                                                                "name": "Roumgu",
                                                                "value": "roumgu-xxx"
                                                },
                                                {
                                                                "name": "Rudolph",
                                                                "value": "rudolph"
                                                },
                                                {
                                                                "name": "Rumi Usagiyama Hentai",
                                                                "value": "rumi-usagiyama-hentai"
                                                },
                                                {
                                                                "name": "Sailor Moon Hentai",
                                                                "value": "sailor-moon-hentai"
                                                },
                                                {
                                                                "name": "Sakura Hentai",
                                                                "value": "sakura_h3ntai"
                                                },
                                                {
                                                                "name": "Sally",
                                                                "value": "sally"
                                                },
                                                {
                                                                "name": "Samantha Manson",
                                                                "value": "samantha-manson"
                                                },
                                                {
                                                                "name": "Samsung Sam Hentai",
                                                                "value": "samsung-sam-hentai"
                                                },
                                                {
                                                                "name": "Samus Hentai",
                                                                "value": "samus-hentai"
                                                },
                                                {
                                                                "name": "Sandy",
                                                                "value": "sandy"
                                                },
                                                {
                                                                "name": "Sarada Hentai",
                                                                "value": "sarada-hentai"
                                                },
                                                {
                                                                "name": "Scooby Doo Hentai",
                                                                "value": "scooby-doo-hentai"
                                                },
                                                {
                                                                "name": "Seiko Hentai",
                                                                "value": "seiko-hentai"
                                                },
                                                {
                                                                "name": "Seraphine",
                                                                "value": "seraphine"
                                                },
                                                {
                                                                "name": "Sex Toys",
                                                                "value": "sex-toys"
                                                },
                                                {
                                                                "name": "Shadbase",
                                                                "value": "shadbase-comics"
                                                },
                                                {
                                                                "name": "Shantae",
                                                                "value": "shantae-hentai"
                                                },
                                                {
                                                                "name": "She-Hulk",
                                                                "value": "she-hulk-porn"
                                                },
                                                {
                                                                "name": "She-Ra and the Princesses of Power Porn",
                                                                "value": "she-ra-and-the-princesses-of-power-porn"
                                                },
                                                {
                                                                "name": "Shego",
                                                                "value": "shego"
                                                },
                                                {
                                                                "name": "shin-chan",
                                                                "value": "shin-chan"
                                                },
                                                {
                                                                "name": "Sidney",
                                                                "value": "sidney"
                                                },
                                                {
                                                                "name": "Sillygirl",
                                                                "value": "sillygirl"
                                                },
                                                {
                                                                "name": "Six",
                                                                "value": "six"
                                                },
                                                {
                                                                "name": "Skullgirls",
                                                                "value": "skullgirls"
                                                },
                                                {
                                                                "name": "Small Tits",
                                                                "value": "small-tits"
                                                },
                                                {
                                                                "name": "Smash Bros",
                                                                "value": "smash-bros"
                                                },
                                                {
                                                                "name": "Sonic",
                                                                "value": "sonic"
                                                },
                                                {
                                                                "name": "Soraka",
                                                                "value": "soraka"
                                                },
                                                {
                                                                "name": "Soraka Hentai",
                                                                "value": "soraka-hentai"
                                                },
                                                {
                                                                "name": "Soul Knight",
                                                                "value": "soul-knight"
                                                },
                                                {
                                                                "name": "South Park",
                                                                "value": "south-park"
                                                },
                                                {
                                                                "name": "Spectra",
                                                                "value": "spectra"
                                                },
                                                {
                                                                "name": "spidergirl",
                                                                "value": "spidergirl-xxx"
                                                },
                                                {
                                                                "name": "Spiderman Porn Comics",
                                                                "value": "porn-comics-xxx-spiderman1"
                                                },
                                                {
                                                                "name": "Spinel",
                                                                "value": "spinel"
                                                },
                                                {
                                                                "name": "Splatoon Porn Comics",
                                                                "value": "splaton-porn-comic"
                                                },
                                                {
                                                                "name": "Spongebob Porn Comics",
                                                                "value": "spongebob-porn-comics"
                                                },
                                                {
                                                                "name": "Spy x Family Porn",
                                                                "value": "p0rn-spy-x-family"
                                                },
                                                {
                                                                "name": "Squirrel Girl Porn",
                                                                "value": "squirrel-girl-porn"
                                                },
                                                {
                                                                "name": "Star Butterfly Porn",
                                                                "value": "star-butterfly-porn"
                                                },
                                                {
                                                                "name": "Star Trek",
                                                                "value": "star-trek"
                                                },
                                                {
                                                                "name": "Star vs The Forces of Evil",
                                                                "value": "porn-comic-star-vs-the-forces-of-evil"
                                                },
                                                {
                                                                "name": "Star Wars",
                                                                "value": "star-wars"
                                                },
                                                {
                                                                "name": "Starfire",
                                                                "value": "starfire"
                                                },
                                                {
                                                                "name": "Steven Universe Porn Comics",
                                                                "value": "steven-universe-porn_comics1"
                                                },
                                                {
                                                                "name": "StormFedeR",
                                                                "value": "stormfeder-porn"
                                                },
                                                {
                                                                "name": "Street Fighter Porn",
                                                                "value": "street-fighter-porn-comix1"
                                                },
                                                {
                                                                "name": "Strong Bana",
                                                                "value": "strong-bana"
                                                },
                                                {
                                                                "name": "StrongBana",
                                                                "value": "strongbana-porn"
                                                },
                                                {
                                                                "name": "Suguha Hentai",
                                                                "value": "suguha-hentai"
                                                },
                                                {
                                                                "name": "Summer Hentai",
                                                                "value": "summer-hentai"
                                                },
                                                {
                                                                "name": "Super Melons",
                                                                "value": "super-melons"
                                                },
                                                {
                                                                "name": "Supergirl",
                                                                "value": "supergirl"
                                                },
                                                {
                                                                "name": "Superman",
                                                                "value": "superman-x"
                                                },
                                                {
                                                                "name": "Taboolicious",
                                                                "value": "taboolicious"
                                                },
                                                {
                                                                "name": "Taimanin Asagi",
                                                                "value": "taimanin-asagi"
                                                },
                                                {
                                                                "name": "Tatsumaki Hentai",
                                                                "value": "tatsumaki-hentai-one-punch-mam"
                                                },
                                                {
                                                                "name": "Tawna",
                                                                "value": "tawna"
                                                },
                                                {
                                                                "name": "Teacher",
                                                                "value": "teacher"
                                                },
                                                {
                                                                "name": "Teen",
                                                                "value": "teen"
                                                },
                                                {
                                                                "name": "Teenage Mutant Ninja Turtles Porn Comics",
                                                                "value": "teenage-mutant-ninja-turtles-porn-comics"
                                                },
                                                {
                                                                "name": "Teens",
                                                                "value": "teens"
                                                },
                                                {
                                                                "name": "Tenn",
                                                                "value": "tenn"
                                                },
                                                {
                                                                "name": "Tentacles",
                                                                "value": "tentacles"
                                                },
                                                {
                                                                "name": "Tenten Hentai",
                                                                "value": "tenten-hentai"
                                                },
                                                {
                                                                "name": "the addams family",
                                                                "value": "the-addams-family"
                                                },
                                                {
                                                                "name": "The Amazing Digital Circus",
                                                                "value": "the-amazing-digital-circus"
                                                },
                                                {
                                                                "name": "The Amazing world of Gumball Porn Comics",
                                                                "value": "amazing-world-of-gumball-porn-comixs"
                                                },
                                                {
                                                                "name": "The Arthman",
                                                                "value": "tthe-arthman"
                                                },
                                                {
                                                                "name": "the elder scrolls",
                                                                "value": "the-elder-scrolls"
                                                },
                                                {
                                                                "name": "The Fairly OddParents Porn",
                                                                "value": "porn-comics-the-fairly-oddparents-xx"
                                                },
                                                {
                                                                "name": "The Grim Adventures of Billy & Mandy Porn",
                                                                "value": "the-grim-adventures-of-billy-mandy-porn"
                                                },
                                                {
                                                                "name": "The grim adventures of Billy and Mandy",
                                                                "value": "the-grim-adventures-of-billy-and-mandy"
                                                },
                                                {
                                                                "name": "The Idolmaster",
                                                                "value": "the-idolmaster"
                                                },
                                                {
                                                                "name": "The Incredibles Porn",
                                                                "value": "the-incredibles-porn_comics"
                                                },
                                                {
                                                                "name": "The Jetsons",
                                                                "value": "the-jetsons"
                                                },
                                                {
                                                                "name": "The Last Airbender",
                                                                "value": "the-last-airbender"
                                                },
                                                {
                                                                "name": "The Last of Us",
                                                                "value": "the-last-of-us"
                                                },
                                                {
                                                                "name": "The Legend of Zelda Hentai",
                                                                "value": "the-legend-of-zelda-p"
                                                },
                                                {
                                                                "name": "The Loud House",
                                                                "value": "porn-comic-the-loud-house-sex"
                                                },
                                                {
                                                                "name": "The Owl House",
                                                                "value": "porn-comics-the-owl-house"
                                                },
                                                {
                                                                "name": "the powerpuff girls",
                                                                "value": "the-powerpuff-girls"
                                                },
                                                {
                                                                "name": "The proud family",
                                                                "value": "the-proud-family"
                                                },
                                                {
                                                                "name": "The Ring",
                                                                "value": "the-ring"
                                                },
                                                {
                                                                "name": "the seven deadly sins",
                                                                "value": "the-seven-deadly-sins"
                                                },
                                                {
                                                                "name": "The Summoning Porn",
                                                                "value": "the-summoning-porn"
                                                },
                                                {
                                                                "name": "The Sword In The Stone Porn Comics",
                                                                "value": "the-sword-in-the-stone-porn-comics"
                                                },
                                                {
                                                                "name": "The Walking Dead",
                                                                "value": "the-walking-dead"
                                                },
                                                {
                                                                "name": "The Witcher",
                                                                "value": "the-witcher"
                                                },
                                                {
                                                                "name": "Thicc",
                                                                "value": "thicc-xxx"
                                                },
                                                {
                                                                "name": "Three Houses",
                                                                "value": "three-houses"
                                                },
                                                {
                                                                "name": "Tifa Hentai",
                                                                "value": "tifa-hentai"
                                                },
                                                {
                                                                "name": "To Love-Ru Hentai",
                                                                "value": "to-love-ru-hentai"
                                                },
                                                {
                                                                "name": "Tomb Raider",
                                                                "value": "tomb-raider"
                                                },
                                                {
                                                                "name": "Tomgirl",
                                                                "value": "tomgirl"
                                                },
                                                {
                                                                "name": "Toph Hentai",
                                                                "value": "toph-hentai"
                                                },
                                                {
                                                                "name": "Total Drama",
                                                                "value": "total-drama-porn-comic"
                                                },
                                                {
                                                                "name": "Totally Spies",
                                                                "value": "porn-comics-totally-spies"
                                                },
                                                {
                                                                "name": "Touhou Project Hentai",
                                                                "value": "touhou-project-hentaix"
                                                },
                                                {
                                                                "name": "Tracy Scops",
                                                                "value": "tracy-scops"
                                                },
                                                {
                                                                "name": "Transformers Porn",
                                                                "value": "transformers-porn"
                                                },
                                                {
                                                                "name": "Tricia Hentai",
                                                                "value": "tricia-hentai"
                                                },
                                                {
                                                                "name": "Tsunade Hentai",
                                                                "value": "tsunade_hentai"
                                                },
                                                {
                                                                "name": "Tsuyu Asui Hentai",
                                                                "value": "tsuyu-asui-hentai"
                                                },
                                                {
                                                                "name": "Umbreon",
                                                                "value": "umbreon"
                                                },
                                                {
                                                                "name": "Uncensored",
                                                                "value": "uncensored"
                                                },
                                                {
                                                                "name": "Undertale",
                                                                "value": "undertale"
                                                },
                                                {
                                                                "name": "Undyne",
                                                                "value": "undyne"
                                                },
                                                {
                                                                "name": "Uraraka Hentai",
                                                                "value": "uraraka-hentai-xxx"
                                                },
                                                {
                                                                "name": "Urbosa",
                                                                "value": "urbosa"
                                                },
                                                {
                                                                "name": "Vados Hentai",
                                                                "value": "vados-hentai"
                                                },
                                                {
                                                                "name": "Vanilla",
                                                                "value": "vanilla"
                                                },
                                                {
                                                                "name": "Velma Hentai",
                                                                "value": "velma-hentai"
                                                },
                                                {
                                                                "name": "VerComicsPorno",
                                                                "value": "vercomicsporno"
                                                },
                                                {
                                                                "name": "Vicky",
                                                                "value": "vicky"
                                                },
                                                {
                                                                "name": "Videl Hentai",
                                                                "value": "videl-hentai1"
                                                },
                                                {
                                                                "name": "violet parr",
                                                                "value": "violet-parr"
                                                },
                                                {
                                                                "name": "Wakfu Porn",
                                                                "value": "wakfu-porn"
                                                },
                                                {
                                                                "name": "Wander Over Yonder",
                                                                "value": "wander-over-yonder"
                                                },
                                                {
                                                                "name": "Warcraft",
                                                                "value": "warcraft"
                                                },
                                                {
                                                                "name": "Warhammer porn",
                                                                "value": "warhammer-porn"
                                                },
                                                {
                                                                "name": "Wendy",
                                                                "value": "wendy"
                                                },
                                                {
                                                                "name": "Widowmaker",
                                                                "value": "widowmaker"
                                                },
                                                {
                                                                "name": "Willow",
                                                                "value": "willow"
                                                },
                                                {
                                                                "name": "Wonder Woman",
                                                                "value": "xxx-wonder-woman"
                                                },
                                                {
                                                                "name": "World of Warcraft",
                                                                "value": "world-of-warcraft"
                                                },
                                                {
                                                                "name": "WoW",
                                                                "value": "wow"
                                                },
                                                {
                                                                "name": "x-men",
                                                                "value": "x-men"
                                                },
                                                {
                                                                "name": "Xayah",
                                                                "value": "xayah"
                                                },
                                                {
                                                                "name": "Xenoblade Chronicles 2 Hentai",
                                                                "value": "xenoblade-chronicles-2-hentaix"
                                                },
                                                {
                                                                "name": "Xierra099",
                                                                "value": "xierra099"
                                                },
                                                {
                                                                "name": "Y3DF",
                                                                "value": "y3df"
                                                },
                                                {
                                                                "name": "Yamato Hentai",
                                                                "value": "yamato-hentai"
                                                },
                                                {
                                                                "name": "Yu Takeyama Hentai",
                                                                "value": "yu-takeyama-hentai"
                                                },
                                                {
                                                                "name": "Yu-Gi-Oh Hentai",
                                                                "value": "yu-gi-oh-entai"
                                                },
                                                {
                                                                "name": "Yuri",
                                                                "value": "yuri-hentai"
                                                },
                                                {
                                                                "name": "Zac",
                                                                "value": "zac"
                                                },
                                                {
                                                                "name": "Zelda Hentai",
                                                                "value": "zelda-hentai"
                                                },
                                                {
                                                                "name": "Zenless Zone Zero porn",
                                                                "value": "zenless-zone-zero-porn"
                                                },
                                                {
                                                                "name": "Zoe",
                                                                "value": "zoe"
                                                },
                                                {
                                                                "name": "Zootopia",
                                                                "value": "zootopia"
                                                }
                                ],
                                "default": "3d-porn-comics-xxx"
                }
]
        return [SourceFilter(**item) for item in data]

    name = 'kingcomix_en'
    display_name = 'KingComiX'
    base_url = 'https://kingcomix.com'
    language = 'en'
    requests_per_minute = 60


SOURCE = GeneratedGenericSource
