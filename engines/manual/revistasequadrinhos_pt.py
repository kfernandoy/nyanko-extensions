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
            # Tiene que quedar ALGO despues de la ruta. `/manga/` a secas es el indice del
            # custom post type, no una serie, y colaba en el listado como una entrada
            # fantasma sin portada titulada "MANGA" (manhuarm, tanto en browse como en
            # search). La guarda de arriba solo limitaba el maximo de segmentos.
            if len(parts) <= route_index + 1:
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
                                                                "name": "Ben 10",
                                                                "value": "ben-10"
                                                },
                                                {
                                                                "name": "Blacknwhite",
                                                                "value": "blacknwhite"
                                                },
                                                {
                                                                "name": "Caiu na Net",
                                                                "value": "caiu-na-net"
                                                },
                                                {
                                                                "name": "Cartoon Comic",
                                                                "value": "cartoon-comic"
                                                },
                                                {
                                                                "name": "Comics",
                                                                "value": "comics"
                                                },
                                                {
                                                                "name": "Comics Tube",
                                                                "value": "comics-tube"
                                                },
                                                {
                                                                "name": "Conto Erótico",
                                                                "value": "conto-erotico"
                                                },
                                                {
                                                                "name": "CrazyDad3D",
                                                                "value": "crazydad3d"
                                                },
                                                {
                                                                "name": "Croc",
                                                                "value": "croc"
                                                },
                                                {
                                                                "name": "Daval3d",
                                                                "value": "daval3d"
                                                },
                                                {
                                                                "name": "Dragon Ball",
                                                                "value": "dragon-ball-super"
                                                },
                                                {
                                                                "name": "English",
                                                                "value": "english"
                                                },
                                                {
                                                                "name": "Erotic Comic",
                                                                "value": "erotic-comic"
                                                },
                                                {
                                                                "name": "Espanhol",
                                                                "value": "espanhol"
                                                },
                                                {
                                                                "name": "Famosas Nuas",
                                                                "value": "famosas-nuas"
                                                },
                                                {
                                                                "name": "Felsala",
                                                                "value": "felsala"
                                                },
                                                {
                                                                "name": "Fred Perry",
                                                                "value": "fred-perry"
                                                },
                                                {
                                                                "name": "Furry",
                                                                "value": "furry"
                                                },
                                                {
                                                                "name": "Futanari",
                                                                "value": "futanari"
                                                },
                                                {
                                                                "name": "Gay Comics",
                                                                "value": "gay-comics"
                                                },
                                                {
                                                                "name": "Gilftoon",
                                                                "value": "gilftoon"
                                                },
                                                {
                                                                "name": "Gravity Falls",
                                                                "value": "gravity-falls"
                                                },
                                                {
                                                                "name": "Hentai",
                                                                "value": "hentai"
                                                },
                                                {
                                                                "name": "Hora de Aventura",
                                                                "value": "hora-de-aventura"
                                                },
                                                {
                                                                "name": "HQ 3D",
                                                                "value": "hq-3d"
                                                },
                                                {
                                                                "name": "HQ ADULTO",
                                                                "value": "hq-adulto"
                                                },
                                                {
                                                                "name": "HQ COMICS",
                                                                "value": "hq-comics"
                                                },
                                                {
                                                                "name": "HQ Furry",
                                                                "value": "hq-furry"
                                                },
                                                {
                                                                "name": "INCESTO",
                                                                "value": "incesto"
                                                },
                                                {
                                                                "name": "Inter-Racial",
                                                                "value": "inter-racial"
                                                },
                                                {
                                                                "name": "Jay-Marvel",
                                                                "value": "jay-marvel"
                                                },
                                                {
                                                                "name": "John Persons",
                                                                "value": "john-persons"
                                                },
                                                {
                                                                "name": "Kaos Comics",
                                                                "value": "kaos-comics"
                                                },
                                                {
                                                                "name": "League of Legends",
                                                                "value": "league-of-legends"
                                                },
                                                {
                                                                "name": "MelkorMancin",
                                                                "value": "melkormancin"
                                                },
                                                {
                                                                "name": "Milftoon",
                                                                "value": "milftoon"
                                                },
                                                {
                                                                "name": "Naruto",
                                                                "value": "naruto"
                                                },
                                                {
                                                                "name": "Os Simpsons",
                                                                "value": "os-simpsons"
                                                },
                                                {
                                                                "name": "Palcomix",
                                                                "value": "palcomix"
                                                },
                                                {
                                                                "name": "Paródias",
                                                                "value": "parodias"
                                                },
                                                {
                                                                "name": "Pegasus Smith",
                                                                "value": "pegasus-smith"
                                                },
                                                {
                                                                "name": "Peitos grandes",
                                                                "value": "peitos-grandes"
                                                },
                                                {
                                                                "name": "PigKing",
                                                                "value": "pigking"
                                                },
                                                {
                                                                "name": "Pokemon",
                                                                "value": "pokemon"
                                                },
                                                {
                                                                "name": "Popular Comics",
                                                                "value": "popular-comics"
                                                },
                                                {
                                                                "name": "Quadrinhos Eróticos",
                                                                "value": "quadrinhos-eroticos"
                                                },
                                                {
                                                                "name": "Revista Playboy",
                                                                "value": "revista-playboy"
                                                },
                                                {
                                                                "name": "Revista Sexy",
                                                                "value": "revista-sexy"
                                                },
                                                {
                                                                "name": "Revistas",
                                                                "value": "revistas"
                                                },
                                                {
                                                                "name": "Seiren",
                                                                "value": "seiren"
                                                },
                                                {
                                                                "name": "Sexy Clube",
                                                                "value": "sexy-clube"
                                                },
                                                {
                                                                "name": "Shemale",
                                                                "value": "shemale"
                                                },
                                                {
                                                                "name": "Spanish",
                                                                "value": "spanish"
                                                },
                                                {
                                                                "name": "Super-Heroínas",
                                                                "value": "super-heroinas"
                                                },
                                                {
                                                                "name": "Tracy Scops",
                                                                "value": "tracy-scops"
                                                },
                                                {
                                                                "name": "Tradução Exclusiva",
                                                                "value": "traducao-exclussiva"
                                                },
                                                {
                                                                "name": "VCP",
                                                                "value": "vcp"
                                                },
                                                {
                                                                "name": "Y3DF",
                                                                "value": "y3df"
                                                },
                                                {
                                                                "name": "3D",
                                                                "value": "3d"
                                                },
                                                {
                                                                "name": "3D Comix",
                                                                "value": "3d-comix"
                                                },
                                                {
                                                                "name": "3d incest",
                                                                "value": "3d-incest"
                                                },
                                                {
                                                                "name": "3D Porn Comic",
                                                                "value": "3d-porn-comic"
                                                },
                                                {
                                                                "name": "3D Porn Comicy3df",
                                                                "value": "3d-porn-comicy3df"
                                                },
                                                {
                                                                "name": "A Casa Errada",
                                                                "value": "a-casa-errada"
                                                },
                                                {
                                                                "name": "academia",
                                                                "value": "academia"
                                                },
                                                {
                                                                "name": "Accel Art",
                                                                "value": "accel-art"
                                                },
                                                {
                                                                "name": "adventure",
                                                                "value": "adventure"
                                                },
                                                {
                                                                "name": "Adventures",
                                                                "value": "adventures"
                                                },
                                                {
                                                                "name": "After Party 02",
                                                                "value": "after-party-02"
                                                },
                                                {
                                                                "name": "ahegao",
                                                                "value": "ahegao"
                                                },
                                                {
                                                                "name": "Ai Papai",
                                                                "value": "ai-papai"
                                                },
                                                {
                                                                "name": "alien",
                                                                "value": "alien"
                                                },
                                                {
                                                                "name": "alien girl",
                                                                "value": "alien-girl"
                                                },
                                                {
                                                                "name": "alongamento",
                                                                "value": "alongamento"
                                                },
                                                {
                                                                "name": "Amanda",
                                                                "value": "amanda"
                                                },
                                                {
                                                                "name": "anal",
                                                                "value": "anal"
                                                },
                                                {
                                                                "name": "anal sex",
                                                                "value": "anal-sex"
                                                },
                                                {
                                                                "name": "android 18",
                                                                "value": "android-18"
                                                },
                                                {
                                                                "name": "Animal",
                                                                "value": "animal"
                                                },
                                                {
                                                                "name": "anime",
                                                                "value": "anime"
                                                },
                                                {
                                                                "name": "armadilha",
                                                                "value": "armadilha"
                                                },
                                                {
                                                                "name": "aroma sensei",
                                                                "value": "aroma-sensei"
                                                },
                                                {
                                                                "name": "As aventuras de Lia",
                                                                "value": "as-aventuras-de-lia"
                                                },
                                                {
                                                                "name": "As Blogueirinhas",
                                                                "value": "as-blogueirinhas"
                                                },
                                                {
                                                                "name": "ass expansion",
                                                                "value": "ass-expansion"
                                                },
                                                {
                                                                "name": "aventura",
                                                                "value": "aventura"
                                                },
                                                {
                                                                "name": "Aventuras",
                                                                "value": "aventuras"
                                                },
                                                {
                                                                "name": "avó",
                                                                "value": "avo"
                                                },
                                                {
                                                                "name": "bart simpson",
                                                                "value": "bart-simpson"
                                                },
                                                {
                                                                "name": "Batman",
                                                                "value": "batman"
                                                },
                                                {
                                                                "name": "Bbm",
                                                                "value": "bbm"
                                                },
                                                {
                                                                "name": "bbw",
                                                                "value": "bbw"
                                                },
                                                {
                                                                "name": "bdsm",
                                                                "value": "bdsm"
                                                },
                                                {
                                                                "name": "bdsm-bondage",
                                                                "value": "bdsm-bondage"
                                                },
                                                {
                                                                "name": "beauty mark",
                                                                "value": "beauty-mark"
                                                },
                                                {
                                                                "name": "bella da semana",
                                                                "value": "bella-da-semana"
                                                },
                                                {
                                                                "name": "Big Ass",
                                                                "value": "big-ass"
                                                },
                                                {
                                                                "name": "big balls",
                                                                "value": "big-balls"
                                                },
                                                {
                                                                "name": "big black dick",
                                                                "value": "big-black-dick"
                                                },
                                                {
                                                                "name": "Big Boobs",
                                                                "value": "big-boobs"
                                                },
                                                {
                                                                "name": "big breast",
                                                                "value": "big-breast"
                                                },
                                                {
                                                                "name": "big breasts",
                                                                "value": "big-breasts"
                                                },
                                                {
                                                                "name": "Big Cock",
                                                                "value": "big-cock"
                                                },
                                                {
                                                                "name": "big dick",
                                                                "value": "big-dick"
                                                },
                                                {
                                                                "name": "big lips",
                                                                "value": "big-lips"
                                                },
                                                {
                                                                "name": "big penis",
                                                                "value": "big-penis"
                                                },
                                                {
                                                                "name": "big tits",
                                                                "value": "big-tits"
                                                },
                                                {
                                                                "name": "bigass",
                                                                "value": "bigass"
                                                },
                                                {
                                                                "name": "bikini",
                                                                "value": "bikini"
                                                },
                                                {
                                                                "name": "biquíni",
                                                                "value": "biquini"
                                                },
                                                {
                                                                "name": "bisexual",
                                                                "value": "bisexual"
                                                },
                                                {
                                                                "name": "black cock",
                                                                "value": "black-cock"
                                                },
                                                {
                                                                "name": "blackmail",
                                                                "value": "blackmail"
                                                },
                                                {
                                                                "name": "blacknwhite",
                                                                "value": "blacknwhite"
                                                },
                                                {
                                                                "name": "BlackNWhitecomics",
                                                                "value": "blacknwhitecomics"
                                                },
                                                {
                                                                "name": "blonde",
                                                                "value": "blonde"
                                                },
                                                {
                                                                "name": "blowjob",
                                                                "value": "blowjob"
                                                },
                                                {
                                                                "name": "Boceta",
                                                                "value": "boceta"
                                                },
                                                {
                                                                "name": "boceta novinha",
                                                                "value": "boceta-novinha"
                                                },
                                                {
                                                                "name": "bodysuit",
                                                                "value": "bodysuit"
                                                },
                                                {
                                                                "name": "bondage",
                                                                "value": "bondage"
                                                },
                                                {
                                                                "name": "boquete",
                                                                "value": "boquete"
                                                },
                                                {
                                                                "name": "boruto",
                                                                "value": "boruto"
                                                },
                                                {
                                                                "name": "brasilbukkake",
                                                                "value": "brasilbukkake"
                                                },
                                                {
                                                                "name": "breast expansion",
                                                                "value": "breast-expansion"
                                                },
                                                {
                                                                "name": "brinquedos sexuais",
                                                                "value": "brinquedos-sexuais"
                                                },
                                                {
                                                                "name": "bro-sis",
                                                                "value": "bro-sis"
                                                },
                                                {
                                                                "name": "buceta",
                                                                "value": "buceta"
                                                },
                                                {
                                                                "name": "buceta da novinha",
                                                                "value": "buceta-da-novinha"
                                                },
                                                {
                                                                "name": "buceta novinha",
                                                                "value": "buceta-novinha"
                                                },
                                                {
                                                                "name": "bukkake",
                                                                "value": "bukkake"
                                                },
                                                {
                                                                "name": "bunda",
                                                                "value": "bunda"
                                                },
                                                {
                                                                "name": "bunda da novinha",
                                                                "value": "bunda-da-novinha"
                                                },
                                                {
                                                                "name": "bunda grande",
                                                                "value": "bunda-grande"
                                                },
                                                {
                                                                "name": "bunny girl",
                                                                "value": "bunny-girl"
                                                },
                                                {
                                                                "name": "busty",
                                                                "value": "busty"
                                                },
                                                {
                                                                "name": "camshot",
                                                                "value": "camshot"
                                                },
                                                {
                                                                "name": "caricaturas",
                                                                "value": "caricaturas"
                                                },
                                                {
                                                                "name": "Cartoon",
                                                                "value": "cartoon"
                                                },
                                                {
                                                                "name": "Cartoon Comics",
                                                                "value": "cartoon-comics"
                                                },
                                                {
                                                                "name": "Cartoon Porno",
                                                                "value": "cartoon-porno"
                                                },
                                                {
                                                                "name": "Cartoon Reality",
                                                                "value": "cartoon-reality"
                                                },
                                                {
                                                                "name": "Casa Bonita 6",
                                                                "value": "casa-bonita-6"
                                                },
                                                {
                                                                "name": "Casa da Mãe Joana",
                                                                "value": "casa-da-mae-joana"
                                                },
                                                {
                                                                "name": "casada",
                                                                "value": "casada"
                                                },
                                                {
                                                                "name": "casadas",
                                                                "value": "casadas"
                                                },
                                                {
                                                                "name": "catboy",
                                                                "value": "catboy"
                                                },
                                                {
                                                                "name": "catgirl",
                                                                "value": "catgirl"
                                                },
                                                {
                                                                "name": "celebridades",
                                                                "value": "celebridades"
                                                },
                                                {
                                                                "name": "Celebridades nuas",
                                                                "value": "celebridades-nuas"
                                                },
                                                {
                                                                "name": "Chaves",
                                                                "value": "chaves"
                                                },
                                                {
                                                                "name": "cheating",
                                                                "value": "cheating"
                                                },
                                                {
                                                                "name": "chesare",
                                                                "value": "chesare"
                                                },
                                                {
                                                                "name": "chichi",
                                                                "value": "chichi"
                                                },
                                                {
                                                                "name": "Chloe",
                                                                "value": "chloe"
                                                },
                                                {
                                                                "name": "Coelhinha",
                                                                "value": "coelhinha"
                                                },
                                                {
                                                                "name": "collar",
                                                                "value": "collar"
                                                },
                                                {
                                                                "name": "Colorido",
                                                                "value": "colorido"
                                                },
                                                {
                                                                "name": "comic",
                                                                "value": "comic"
                                                },
                                                {
                                                                "name": "Comiccrazydad3d",
                                                                "value": "comiccrazydad3d"
                                                },
                                                {
                                                                "name": "Comics Porno",
                                                                "value": "comics-porno"
                                                },
                                                {
                                                                "name": "Comix",
                                                                "value": "comix"
                                                },
                                                {
                                                                "name": "condom",
                                                                "value": "condom"
                                                },
                                                {
                                                                "name": "Contos Eróticos",
                                                                "value": "contos-eroticos"
                                                },
                                                {
                                                                "name": "controle mental",
                                                                "value": "controle-mental"
                                                },
                                                {
                                                                "name": "corno",
                                                                "value": "corno"
                                                },
                                                {
                                                                "name": "corruption",
                                                                "value": "corruption"
                                                },
                                                {
                                                                "name": "Cousin",
                                                                "value": "cousin"
                                                },
                                                {
                                                                "name": "cowgirl",
                                                                "value": "cowgirl"
                                                },
                                                {
                                                                "name": "crazydad3d",
                                                                "value": "crazydad3d"
                                                },
                                                {
                                                                "name": "Creampie",
                                                                "value": "creampie"
                                                },
                                                {
                                                                "name": "crempie",
                                                                "value": "crempie"
                                                },
                                                {
                                                                "name": "crossdressing",
                                                                "value": "crossdressing"
                                                },
                                                {
                                                                "name": "cuckold",
                                                                "value": "cuckold"
                                                },
                                                {
                                                                "name": "cum shot",
                                                                "value": "cum-shot"
                                                },
                                                {
                                                                "name": "Cum shots",
                                                                "value": "cum-shots"
                                                },
                                                {
                                                                "name": "Cum swallow",
                                                                "value": "cum-swallow"
                                                },
                                                {
                                                                "name": "cumshot",
                                                                "value": "cumshot"
                                                },
                                                {
                                                                "name": "cunilíngua",
                                                                "value": "cunilingua"
                                                },
                                                {
                                                                "name": "Cunnilingus",
                                                                "value": "cunnilingus"
                                                },
                                                {
                                                                "name": "Dad-Daughter",
                                                                "value": "dad-daughter"
                                                },
                                                {
                                                                "name": "Danny Phantom",
                                                                "value": "danny-phantom"
                                                },
                                                {
                                                                "name": "dark skin",
                                                                "value": "dark-skin"
                                                },
                                                {
                                                                "name": "daughter",
                                                                "value": "daughter"
                                                },
                                                {
                                                                "name": "daval3d",
                                                                "value": "daval3d"
                                                },
                                                {
                                                                "name": "deepthroat",
                                                                "value": "deepthroat"
                                                },
                                                {
                                                                "name": "Defloração",
                                                                "value": "defloracao"
                                                },
                                                {
                                                                "name": "Deformed",
                                                                "value": "deformed"
                                                },
                                                {
                                                                "name": "demon girl",
                                                                "value": "demon-girl"
                                                },
                                                {
                                                                "name": "Detenção",
                                                                "value": "detencao"
                                                },
                                                {
                                                                "name": "dickgirl",
                                                                "value": "dickgirl"
                                                },
                                                {
                                                                "name": "dickgirl on male",
                                                                "value": "dickgirl-on-male"
                                                },
                                                {
                                                                "name": "dilf",
                                                                "value": "dilf"
                                                },
                                                {
                                                                "name": "dirtycomics",
                                                                "value": "dirtycomics"
                                                },
                                                {
                                                                "name": "dog boy",
                                                                "value": "dog-boy"
                                                },
                                                {
                                                                "name": "dog girl",
                                                                "value": "dog-girl"
                                                },
                                                {
                                                                "name": "doggystyle",
                                                                "value": "doggystyle"
                                                },
                                                {
                                                                "name": "Domination",
                                                                "value": "domination"
                                                },
                                                {
                                                                "name": "dominação",
                                                                "value": "dominacao"
                                                },
                                                {
                                                                "name": "dona de casa",
                                                                "value": "dona-de-casa"
                                                },
                                                {
                                                                "name": "Dona Mama",
                                                                "value": "dona-mama"
                                                },
                                                {
                                                                "name": "donas de casa voluptuosas",
                                                                "value": "donas-de-casa-voluptuosas"
                                                },
                                                {
                                                                "name": "dotado",
                                                                "value": "dotado"
                                                },
                                                {
                                                                "name": "Dotados",
                                                                "value": "dotados"
                                                },
                                                {
                                                                "name": "double anal",
                                                                "value": "double-anal"
                                                },
                                                {
                                                                "name": "double penetration",
                                                                "value": "double-penetration"
                                                },
                                                {
                                                                "name": "dragon ball super",
                                                                "value": "dragon-ball-super"
                                                },
                                                {
                                                                "name": "dragon ball x",
                                                                "value": "dragon-ball-x"
                                                },
                                                {
                                                                "name": "dragon ball z",
                                                                "value": "dragon-ball-z"
                                                },
                                                {
                                                                "name": "Drah Navlag",
                                                                "value": "drah-navlag"
                                                },
                                                {
                                                                "name": "Drawn Sex",
                                                                "value": "drawn-sex"
                                                },
                                                {
                                                                "name": "Drunk",
                                                                "value": "drunk"
                                                },
                                                {
                                                                "name": "Dsan",
                                                                "value": "dsan"
                                                },
                                                {
                                                                "name": "Dupla penetração",
                                                                "value": "dupla-penetracao"
                                                },
                                                {
                                                                "name": "elf",
                                                                "value": "elf"
                                                },
                                                {
                                                                "name": "elfo",
                                                                "value": "elfo"
                                                },
                                                {
                                                                "name": "emmabrave",
                                                                "value": "emmabrave"
                                                },
                                                {
                                                                "name": "empregada doméstica",
                                                                "value": "empregada-domestica"
                                                },
                                                {
                                                                "name": "Erotic",
                                                                "value": "erotic"
                                                },
                                                {
                                                                "name": "Erotic Comics",
                                                                "value": "erotic-comics"
                                                },
                                                {
                                                                "name": "Eróticos",
                                                                "value": "eroticos"
                                                },
                                                {
                                                                "name": "estilo cachorrinho",
                                                                "value": "estilo-cachorrinho"
                                                },
                                                {
                                                                "name": "Euro",
                                                                "value": "euro"
                                                },
                                                {
                                                                "name": "Exhibitionism",
                                                                "value": "exhibitionism"
                                                },
                                                {
                                                                "name": "expansão de seios",
                                                                "value": "expansao-de-seios"
                                                },
                                                {
                                                                "name": "eyemask",
                                                                "value": "eyemask"
                                                },
                                                {
                                                                "name": "Familia Guy",
                                                                "value": "familia-guy"
                                                },
                                                {
                                                                "name": "family",
                                                                "value": "family"
                                                },
                                                {
                                                                "name": "Family Guy",
                                                                "value": "family-guy"
                                                },
                                                {
                                                                "name": "Family Sex",
                                                                "value": "family-sex"
                                                },
                                                {
                                                                "name": "family-incest",
                                                                "value": "family-incest"
                                                },
                                                {
                                                                "name": "Famosas Gostosas",
                                                                "value": "famosas-gostosas"
                                                },
                                                {
                                                                "name": "famosas nuas",
                                                                "value": "famosas-nuas-2"
                                                },
                                                {
                                                                "name": "Família",
                                                                "value": "familia"
                                                },
                                                {
                                                                "name": "fantasia",
                                                                "value": "fantasia"
                                                },
                                                {
                                                                "name": "Fantasy",
                                                                "value": "fantasy"
                                                },
                                                {
                                                                "name": "father-daughter",
                                                                "value": "father-daughter"
                                                },
                                                {
                                                                "name": "felsala",
                                                                "value": "felsala"
                                                },
                                                {
                                                                "name": "females only",
                                                                "value": "females-only"
                                                },
                                                {
                                                                "name": "femdom",
                                                                "value": "femdom"
                                                },
                                                {
                                                                "name": "feminization",
                                                                "value": "feminization"
                                                },
                                                {
                                                                "name": "feminização",
                                                                "value": "feminizacao"
                                                },
                                                {
                                                                "name": "ffm threesome",
                                                                "value": "ffm-threesome"
                                                },
                                                {
                                                                "name": "filha",
                                                                "value": "filha"
                                                },
                                                {
                                                                "name": "filming",
                                                                "value": "filming"
                                                },
                                                {
                                                                "name": "Fingering",
                                                                "value": "fingering"
                                                },
                                                {
                                                                "name": "fisting",
                                                                "value": "fisting"
                                                },
                                                {
                                                                "name": "foda de peitos",
                                                                "value": "foda-de-peitos"
                                                },
                                                {
                                                                "name": "foda de tetas",
                                                                "value": "foda-de-tetas"
                                                },
                                                {
                                                                "name": "Footjob",
                                                                "value": "footjob"
                                                },
                                                {
                                                                "name": "forced",
                                                                "value": "forced"
                                                },
                                                {
                                                                "name": "fotos amadoras",
                                                                "value": "fotos-amadoras"
                                                },
                                                {
                                                                "name": "Fotos de Bundas",
                                                                "value": "fotos-de-bundas"
                                                },
                                                {
                                                                "name": "Fotos Digitais",
                                                                "value": "fotos-digitais"
                                                },
                                                {
                                                                "name": "freckles",
                                                                "value": "freckles"
                                                },
                                                {
                                                                "name": "fred perry",
                                                                "value": "fred-perry"
                                                },
                                                {
                                                                "name": "freira",
                                                                "value": "freira"
                                                },
                                                {
                                                                "name": "Fright Night",
                                                                "value": "fright-night"
                                                },
                                                {
                                                                "name": "Frozen",
                                                                "value": "frozen"
                                                },
                                                {
                                                                "name": "Full Color",
                                                                "value": "full-color"
                                                },
                                                {
                                                                "name": "futanaria",
                                                                "value": "futanaria"
                                                },
                                                {
                                                                "name": "Gangbang",
                                                                "value": "gangbang"
                                                },
                                                {
                                                                "name": "Garganta Profunda",
                                                                "value": "garganta-profunda"
                                                },
                                                {
                                                                "name": "gay",
                                                                "value": "gay"
                                                },
                                                {
                                                                "name": "Gender Bender",
                                                                "value": "gender-bender"
                                                },
                                                {
                                                                "name": "gilftoon",
                                                                "value": "gilftoon"
                                                },
                                                {
                                                                "name": "glasses",
                                                                "value": "glasses"
                                                },
                                                {
                                                                "name": "gostosa",
                                                                "value": "gostosa"
                                                },
                                                {
                                                                "name": "gozada",
                                                                "value": "gozada"
                                                },
                                                {
                                                                "name": "Gozando a Vida Adoidado",
                                                                "value": "gozando-a-vida-adoidado"
                                                },
                                                {
                                                                "name": "gozar na boca",
                                                                "value": "gozar-na-boca"
                                                },
                                                {
                                                                "name": "grandes paus",
                                                                "value": "grandes-paus"
                                                },
                                                {
                                                                "name": "grandmother",
                                                                "value": "grandmother"
                                                },
                                                {
                                                                "name": "Gravity falls",
                                                                "value": "gravity-falls"
                                                },
                                                {
                                                                "name": "group",
                                                                "value": "group"
                                                },
                                                {
                                                                "name": "Group Sex",
                                                                "value": "group-sex"
                                                },
                                                {
                                                                "name": "grupo",
                                                                "value": "grupo"
                                                },
                                                {
                                                                "name": "H-Mangá",
                                                                "value": "h-manga"
                                                },
                                                {
                                                                "name": "hairy",
                                                                "value": "hairy"
                                                },
                                                {
                                                                "name": "handjob",
                                                                "value": "handjob"
                                                },
                                                {
                                                                "name": "Hardcore",
                                                                "value": "hardcore"
                                                },
                                                {
                                                                "name": "harem",
                                                                "value": "harem"
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
                                                                "name": "Hentai Comics",
                                                                "value": "hentai-comics"
                                                },
                                                {
                                                                "name": "Hentai Incesto",
                                                                "value": "hentai-incesto"
                                                },
                                                {
                                                                "name": "hermit moth",
                                                                "value": "hermit-moth"
                                                },
                                                {
                                                                "name": "Heróis Porno",
                                                                "value": "herois-porno"
                                                },
                                                {
                                                                "name": "high heels",
                                                                "value": "high-heels"
                                                },
                                                {
                                                                "name": "Hinata",
                                                                "value": "hinata"
                                                },
                                                {
                                                                "name": "hinata hyuga",
                                                                "value": "hinata-hyuga"
                                                },
                                                {
                                                                "name": "Homem Aranha",
                                                                "value": "homem-aranha"
                                                },
                                                {
                                                                "name": "Hora de aventura",
                                                                "value": "hora-de-aventura"
                                                },
                                                {
                                                                "name": "housewife",
                                                                "value": "housewife"
                                                },
                                                {
                                                                "name": "HQ",
                                                                "value": "hq"
                                                },
                                                {
                                                                "name": "HQ Adulto",
                                                                "value": "hq-adulto"
                                                },
                                                {
                                                                "name": "HQ Comics",
                                                                "value": "hq-comics"
                                                },
                                                {
                                                                "name": "HQ de Sexo",
                                                                "value": "hq-de-sexo"
                                                },
                                                {
                                                                "name": "HQ Erótico",
                                                                "value": "hq-erotico"
                                                },
                                                {
                                                                "name": "HQ Eróticos Comics",
                                                                "value": "hq-eroticos-comics"
                                                },
                                                {
                                                                "name": "HQPorno",
                                                                "value": "hqporno"
                                                },
                                                {
                                                                "name": "huge breasts",
                                                                "value": "huge-breasts"
                                                },
                                                {
                                                                "name": "huge penis",
                                                                "value": "huge-penis"
                                                },
                                                {
                                                                "name": "human on furry",
                                                                "value": "human-on-furry"
                                                },
                                                {
                                                                "name": "impregnation",
                                                                "value": "impregnation"
                                                },
                                                {
                                                                "name": "impregnação",
                                                                "value": "impregnacao"
                                                },
                                                {
                                                                "name": "incest",
                                                                "value": "incest"
                                                },
                                                {
                                                                "name": "incesto",
                                                                "value": "incesto"
                                                },
                                                {
                                                                "name": "inflation",
                                                                "value": "inflation"
                                                },
                                                {
                                                                "name": "Inter-racial",
                                                                "value": "inter-racial"
                                                },
                                                {
                                                                "name": "Interracial",
                                                                "value": "interracial"
                                                },
                                                {
                                                                "name": "jab-comix",
                                                                "value": "jab-comix"
                                                },
                                                {
                                                                "name": "jabcomix",
                                                                "value": "jabcomix"
                                                },
                                                {
                                                                "name": "jay-marvel",
                                                                "value": "jay-marvel"
                                                },
                                                {
                                                                "name": "jmoz",
                                                                "value": "jmoz"
                                                },
                                                {
                                                                "name": "jmoz comix",
                                                                "value": "jmoz-comix"
                                                },
                                                {
                                                                "name": "Kaos",
                                                                "value": "kaos"
                                                },
                                                {
                                                                "name": "karmagik",
                                                                "value": "karmagik"
                                                },
                                                {
                                                                "name": "kennycomix",
                                                                "value": "kennycomix"
                                                },
                                                {
                                                                "name": "Kim Possible",
                                                                "value": "kim-possible"
                                                },
                                                {
                                                                "name": "kissing",
                                                                "value": "kissing"
                                                },
                                                {
                                                                "name": "Kogeikun",
                                                                "value": "kogeikun"
                                                },
                                                {
                                                                "name": "lactation",
                                                                "value": "lactation"
                                                },
                                                {
                                                                "name": "lambendo buceta",
                                                                "value": "lambendo-buceta"
                                                },
                                                {
                                                                "name": "Lara Croft",
                                                                "value": "lara-croft"
                                                },
                                                {
                                                                "name": "Latex",
                                                                "value": "latex"
                                                },
                                                {
                                                                "name": "leite",
                                                                "value": "leite"
                                                },
                                                {
                                                                "name": "lesbian",
                                                                "value": "lesbian"
                                                },
                                                {
                                                                "name": "Lesbians",
                                                                "value": "lesbians"
                                                },
                                                {
                                                                "name": "Lia",
                                                                "value": "lia"
                                                },
                                                {
                                                                "name": "Lingerie",
                                                                "value": "lingerie"
                                                },
                                                {
                                                                "name": "Locofuria",
                                                                "value": "locofuria"
                                                },
                                                {
                                                                "name": "loiras",
                                                                "value": "loiras"
                                                },
                                                {
                                                                "name": "lésbica",
                                                                "value": "lesbica"
                                                },
                                                {
                                                                "name": "Lésbicas",
                                                                "value": "lesbicas"
                                                },
                                                {
                                                                "name": "madura",
                                                                "value": "madura"
                                                },
                                                {
                                                                "name": "maduro",
                                                                "value": "maduro"
                                                },
                                                {
                                                                "name": "Maid",
                                                                "value": "maid"
                                                },
                                                {
                                                                "name": "males only",
                                                                "value": "males-only"
                                                },
                                                {
                                                                "name": "Manga",
                                                                "value": "manga"
                                                },
                                                {
                                                                "name": "Manga Hentai",
                                                                "value": "manga-hentai"
                                                },
                                                {
                                                                "name": "Mangas",
                                                                "value": "mangas"
                                                },
                                                {
                                                                "name": "mano",
                                                                "value": "mano"
                                                },
                                                {
                                                                "name": "mano-irmã",
                                                                "value": "mano-irma"
                                                },
                                                {
                                                                "name": "Marge simpson",
                                                                "value": "marge-simpson"
                                                },
                                                {
                                                                "name": "maricas",
                                                                "value": "maricas"
                                                },
                                                {
                                                                "name": "masturbation",
                                                                "value": "masturbation"
                                                },
                                                {
                                                                "name": "masturbação",
                                                                "value": "masturbacao"
                                                },
                                                {
                                                                "name": "mature",
                                                                "value": "mature"
                                                },
                                                {
                                                                "name": "meias",
                                                                "value": "meias"
                                                },
                                                {
                                                                "name": "melkor mancin",
                                                                "value": "melkor-mancin"
                                                },
                                                {
                                                                "name": "Melkormancin",
                                                                "value": "melkormancin"
                                                },
                                                {
                                                                "name": "Mexican",
                                                                "value": "mexican"
                                                },
                                                {
                                                                "name": "MILF",
                                                                "value": "milf"
                                                },
                                                {
                                                                "name": "mind control",
                                                                "value": "mind-control"
                                                },
                                                {
                                                                "name": "mom-n-son",
                                                                "value": "mom-n-son"
                                                },
                                                {
                                                                "name": "mom-son",
                                                                "value": "mom-son"
                                                },
                                                {
                                                                "name": "Moms help",
                                                                "value": "moms-help"
                                                },
                                                {
                                                                "name": "mon-n-filho",
                                                                "value": "mon-n-filho"
                                                },
                                                {
                                                                "name": "mon-n-son",
                                                                "value": "mon-n-son"
                                                },
                                                {
                                                                "name": "monster",
                                                                "value": "monster"
                                                },
                                                {
                                                                "name": "monster girl",
                                                                "value": "monster-girl"
                                                },
                                                {
                                                                "name": "Monster Girls",
                                                                "value": "monster-girls"
                                                },
                                                {
                                                                "name": "monsters",
                                                                "value": "monsters"
                                                },
                                                {
                                                                "name": "monstros",
                                                                "value": "monstros"
                                                },
                                                {
                                                                "name": "moose",
                                                                "value": "moose"
                                                },
                                                {
                                                                "name": "MORENA",
                                                                "value": "morena"
                                                },
                                                {
                                                                "name": "mostrando a boceta",
                                                                "value": "mostrando-a-boceta"
                                                },
                                                {
                                                                "name": "mother",
                                                                "value": "mother"
                                                },
                                                {
                                                                "name": "mother and son",
                                                                "value": "mother-and-son"
                                                },
                                                {
                                                                "name": "Multishow",
                                                                "value": "multishow"
                                                },
                                                {
                                                                "name": "muscle",
                                                                "value": "muscle"
                                                },
                                                {
                                                                "name": "my bad bunny",
                                                                "value": "my-bad-bunny"
                                                },
                                                {
                                                                "name": "my dear old sister",
                                                                "value": "my-dear-old-sister"
                                                },
                                                {
                                                                "name": "Mãe",
                                                                "value": "mae"
                                                },
                                                {
                                                                "name": "mãe e filha",
                                                                "value": "mae-e-filha"
                                                },
                                                {
                                                                "name": "Mãe e Filho",
                                                                "value": "mae-e-filho"
                                                },
                                                {
                                                                "name": "músculo",
                                                                "value": "musculo"
                                                },
                                                {
                                                                "name": "nakadashi",
                                                                "value": "nakadashi"
                                                },
                                                {
                                                                "name": "Netorare",
                                                                "value": "netorare"
                                                },
                                                {
                                                                "name": "NLT Comics",
                                                                "value": "nlt-comics"
                                                },
                                                {
                                                                "name": "NLT Media",
                                                                "value": "nlt-media"
                                                },
                                                {
                                                                "name": "novinha",
                                                                "value": "novinha"
                                                },
                                                {
                                                                "name": "novinha nua",
                                                                "value": "novinha-nua"
                                                },
                                                {
                                                                "name": "Novinhas",
                                                                "value": "novinhas"
                                                },
                                                {
                                                                "name": "novinhas nuas",
                                                                "value": "novinhas-nuas"
                                                },
                                                {
                                                                "name": "nua",
                                                                "value": "nua"
                                                },
                                                {
                                                                "name": "Nun Isabella",
                                                                "value": "nun-isabella"
                                                },
                                                {
                                                                "name": "o Sogro Tarado",
                                                                "value": "o-sogro-tarado"
                                                },
                                                {
                                                                "name": "O Sogro Tarado 2",
                                                                "value": "o-sogro-tarado-2"
                                                },
                                                {
                                                                "name": "O Som do Prazer",
                                                                "value": "o-som-do-prazer"
                                                },
                                                {
                                                                "name": "Office",
                                                                "value": "office"
                                                },
                                                {
                                                                "name": "old man",
                                                                "value": "old-man"
                                                },
                                                {
                                                                "name": "oral",
                                                                "value": "oral"
                                                },
                                                {
                                                                "name": "oral sex",
                                                                "value": "oral-sex"
                                                },
                                                {
                                                                "name": "Orc",
                                                                "value": "orc"
                                                },
                                                {
                                                                "name": "orgia",
                                                                "value": "orgia"
                                                },
                                                {
                                                                "name": "orgy",
                                                                "value": "orgy"
                                                },
                                                {
                                                                "name": "Os Flintstones",
                                                                "value": "os-flintstones"
                                                },
                                                {
                                                                "name": "Os Incríveis",
                                                                "value": "os-incriveis"
                                                },
                                                {
                                                                "name": "Os Padrinhos Mágicos",
                                                                "value": "os-padrinhos-magicos"
                                                },
                                                {
                                                                "name": "Os sacanas petisco",
                                                                "value": "os-sacanas-petisco"
                                                },
                                                {
                                                                "name": "Os Simptoons",
                                                                "value": "os-simptoons"
                                                },
                                                {
                                                                "name": "Padrinhos Mágicos",
                                                                "value": "padrinhos-magicos"
                                                },
                                                {
                                                                "name": "Pai e Filha",
                                                                "value": "pai-e-filha"
                                                },
                                                {
                                                                "name": "pai louco",
                                                                "value": "pai-louco"
                                                },
                                                {
                                                                "name": "pai-filha",
                                                                "value": "pai-filha"
                                                },
                                                {
                                                                "name": "paizuri",
                                                                "value": "paizuri"
                                                },
                                                {
                                                                "name": "Papito",
                                                                "value": "papito"
                                                },
                                                {
                                                                "name": "Parodia",
                                                                "value": "parodia"
                                                },
                                                {
                                                                "name": "parodies",
                                                                "value": "parodies"
                                                },
                                                {
                                                                "name": "parody",
                                                                "value": "parody"
                                                },
                                                {
                                                                "name": "Party Time",
                                                                "value": "party-time"
                                                },
                                                {
                                                                "name": "paródias",
                                                                "value": "parodias"
                                                },
                                                {
                                                                "name": "pau grande",
                                                                "value": "pau-grande"
                                                },
                                                {
                                                                "name": "pau no cu",
                                                                "value": "pau-no-cu"
                                                },
                                                {
                                                                "name": "pau preto grande",
                                                                "value": "pau-preto-grande"
                                                },
                                                {
                                                                "name": "pegasus smith",
                                                                "value": "pegasus-smith"
                                                },
                                                {
                                                                "name": "peito grande",
                                                                "value": "peito-grande"
                                                },
                                                {
                                                                "name": "peitos",
                                                                "value": "peitos"
                                                },
                                                {
                                                                "name": "peitos enormes",
                                                                "value": "peitos-enormes"
                                                },
                                                {
                                                                "name": "peitos foda",
                                                                "value": "peitos-foda"
                                                },
                                                {
                                                                "name": "peitos grandes",
                                                                "value": "peitos-grandes"
                                                },
                                                {
                                                                "name": "Peituda",
                                                                "value": "peituda"
                                                },
                                                {
                                                                "name": "peitudas",
                                                                "value": "peitudas"
                                                },
                                                {
                                                                "name": "Peitões",
                                                                "value": "peitoes"
                                                },
                                                {
                                                                "name": "pelada",
                                                                "value": "pelada"
                                                },
                                                {
                                                                "name": "pele escura",
                                                                "value": "pele-escura"
                                                },
                                                {
                                                                "name": "peludo",
                                                                "value": "peludo"
                                                },
                                                {
                                                                "name": "personal trainer",
                                                                "value": "personal-trainer"
                                                },
                                                {
                                                                "name": "piercing",
                                                                "value": "piercing"
                                                },
                                                {
                                                                "name": "pig king",
                                                                "value": "pig-king"
                                                },
                                                {
                                                                "name": "pink pawg",
                                                                "value": "pink-pawg"
                                                },
                                                {
                                                                "name": "ponytail",
                                                                "value": "ponytail"
                                                },
                                                {
                                                                "name": "porcaria",
                                                                "value": "porcaria"
                                                },
                                                {
                                                                "name": "porquinho",
                                                                "value": "porquinho"
                                                },
                                                {
                                                                "name": "Porra",
                                                                "value": "porra"
                                                },
                                                {
                                                                "name": "Portuguese",
                                                                "value": "portuguese"
                                                },
                                                {
                                                                "name": "pregnant",
                                                                "value": "pregnant"
                                                },
                                                {
                                                                "name": "Priminha",
                                                                "value": "priminha"
                                                },
                                                {
                                                                "name": "Priminha Gostosa",
                                                                "value": "priminha-gostosa"
                                                },
                                                {
                                                                "name": "prostitution",
                                                                "value": "prostitution"
                                                },
                                                {
                                                                "name": "punheta",
                                                                "value": "punheta"
                                                },
                                                {
                                                                "name": "pussy licking",
                                                                "value": "pussy-licking"
                                                },
                                                {
                                                                "name": "putinha nerd",
                                                                "value": "putinha-nerd"
                                                },
                                                {
                                                                "name": "pênis grande",
                                                                "value": "penis-grande"
                                                },
                                                {
                                                                "name": "Quadrinhos Eróticos (tag)",
                                                                "value": "quadrinhos-eroticos"
                                                },
                                                {
                                                                "name": "Quadrinhos",
                                                                "value": "quadrinhos"
                                                },
                                                {
                                                                "name": "Quadrinhos Eróticos em português",
                                                                "value": "quadrinhos-eroticos-em-portugues"
                                                },
                                                {
                                                                "name": "rabies",
                                                                "value": "rabies"
                                                },
                                                {
                                                                "name": "Raio-X",
                                                                "value": "raio-x"
                                                },
                                                {
                                                                "name": "rape",
                                                                "value": "rape"
                                                },
                                                {
                                                                "name": "Revistas e Quadrinhos",
                                                                "value": "revistas-e-quadrinhos"
                                                },
                                                {
                                                                "name": "robot",
                                                                "value": "robot"
                                                },
                                                {
                                                                "name": "Ryan",
                                                                "value": "ryan"
                                                },
                                                {
                                                                "name": "salto alto",
                                                                "value": "salto-alto"
                                                },
                                                {
                                                                "name": "Schoolgirl",
                                                                "value": "schoolgirl"
                                                },
                                                {
                                                                "name": "Scooby Doo",
                                                                "value": "scooby-doo"
                                                },
                                                {
                                                                "name": "Seduced",
                                                                "value": "seduced"
                                                },
                                                {
                                                                "name": "Sedução",
                                                                "value": "seducao"
                                                },
                                                {
                                                                "name": "seios enormes",
                                                                "value": "seios-enormes"
                                                },
                                                {
                                                                "name": "seios grandes",
                                                                "value": "seios-grandes"
                                                },
                                                {
                                                                "name": "Sex and Magic",
                                                                "value": "sex-and-magic"
                                                },
                                                {
                                                                "name": "sex toys",
                                                                "value": "sex-toys"
                                                },
                                                {
                                                                "name": "sexgazer",
                                                                "value": "sexgazer"
                                                },
                                                {
                                                                "name": "sexo a três",
                                                                "value": "sexo-a-tres"
                                                },
                                                {
                                                                "name": "sexo anal",
                                                                "value": "sexo-anal"
                                                },
                                                {
                                                                "name": "Sexo em Família",
                                                                "value": "sexo-em-familia"
                                                },
                                                {
                                                                "name": "sexo em grupo",
                                                                "value": "sexo-em-grupo"
                                                },
                                                {
                                                                "name": "Sexo em Quadrinhos",
                                                                "value": "sexo-em-quadrinhos"
                                                },
                                                {
                                                                "name": "sexo grupal",
                                                                "value": "sexo-grupal"
                                                },
                                                {
                                                                "name": "sexo oral",
                                                                "value": "sexo-oral"
                                                },
                                                {
                                                                "name": "sexo vaginal",
                                                                "value": "sexo-vaginal"
                                                },
                                                {
                                                                "name": "Sexy",
                                                                "value": "sexy"
                                                },
                                                {
                                                                "name": "sexy clube",
                                                                "value": "sexy-clube-2"
                                                },
                                                {
                                                                "name": "Sexy Sleep Walking",
                                                                "value": "sexy-sleep-walking"
                                                },
                                                {
                                                                "name": "SexyClube",
                                                                "value": "sexyclube"
                                                },
                                                {
                                                                "name": "Shadbase",
                                                                "value": "shadbase"
                                                },
                                                {
                                                                "name": "shorts de ginástica",
                                                                "value": "shorts-de-ginastica"
                                                },
                                                {
                                                                "name": "Sidney 3",
                                                                "value": "sidney-3"
                                                },
                                                {
                                                                "name": "Simpsons",
                                                                "value": "simpsons"
                                                },
                                                {
                                                                "name": "Sister",
                                                                "value": "sister"
                                                },
                                                {
                                                                "name": "sleinad flar",
                                                                "value": "sleinad-flar"
                                                },
                                                {
                                                                "name": "slut",
                                                                "value": "slut"
                                                },
                                                {
                                                                "name": "small breasts",
                                                                "value": "small-breasts"
                                                },
                                                {
                                                                "name": "smell",
                                                                "value": "smell"
                                                },
                                                {
                                                                "name": "Sogro tarado",
                                                                "value": "sogro-tarado"
                                                },
                                                {
                                                                "name": "sole dickgirl",
                                                                "value": "sole-dickgirl"
                                                },
                                                {
                                                                "name": "sole female",
                                                                "value": "sole-female"
                                                },
                                                {
                                                                "name": "sole male",
                                                                "value": "sole-male"
                                                },
                                                {
                                                                "name": "solo",
                                                                "value": "solo"
                                                },
                                                {
                                                                "name": "son-mom",
                                                                "value": "son-mom"
                                                },
                                                {
                                                                "name": "Sonic",
                                                                "value": "sonic"
                                                },
                                                {
                                                                "name": "spanking",
                                                                "value": "spanking"
                                                },
                                                {
                                                                "name": "Spider Man",
                                                                "value": "spider-man"
                                                },
                                                {
                                                                "name": "Spiderman",
                                                                "value": "spiderman"
                                                },
                                                {
                                                                "name": "spy",
                                                                "value": "spy"
                                                },
                                                {
                                                                "name": "star vs. the forces of evil",
                                                                "value": "star-vs-the-forces-of-evil"
                                                },
                                                {
                                                                "name": "Star Wars",
                                                                "value": "star-wars"
                                                },
                                                {
                                                                "name": "Steven Universe",
                                                                "value": "steven-universe"
                                                },
                                                {
                                                                "name": "stockings",
                                                                "value": "stockings"
                                                },
                                                {
                                                                "name": "stomach deformation",
                                                                "value": "stomach-deformation"
                                                },
                                                {
                                                                "name": "Straight",
                                                                "value": "straight"
                                                },
                                                {
                                                                "name": "Straight Shota",
                                                                "value": "straight-shota"
                                                },
                                                {
                                                                "name": "Suky-Tu",
                                                                "value": "suky-tu"
                                                },
                                                {
                                                                "name": "sunglasses",
                                                                "value": "sunglasses"
                                                },
                                                {
                                                                "name": "super heroina",
                                                                "value": "super-heroina"
                                                },
                                                {
                                                                "name": "super-heroi",
                                                                "value": "super-heroi"
                                                },
                                                {
                                                                "name": "Super-herois",
                                                                "value": "super-herois"
                                                },
                                                {
                                                                "name": "Supergirl",
                                                                "value": "supergirl"
                                                },
                                                {
                                                                "name": "Supergreen",
                                                                "value": "supergreen"
                                                },
                                                {
                                                                "name": "Superheroine",
                                                                "value": "superheroine"
                                                },
                                                {
                                                                "name": "superheros",
                                                                "value": "superheros"
                                                },
                                                {
                                                                "name": "suruba",
                                                                "value": "suruba"
                                                },
                                                {
                                                                "name": "swestern",
                                                                "value": "swestern"
                                                },
                                                {
                                                                "name": "swimsuit",
                                                                "value": "swimsuit"
                                                },
                                                {
                                                                "name": "taboo",
                                                                "value": "taboo"
                                                },
                                                {
                                                                "name": "Taboolicious",
                                                                "value": "taboolicious"
                                                },
                                                {
                                                                "name": "tall girl",
                                                                "value": "tall-girl"
                                                },
                                                {
                                                                "name": "Tara",
                                                                "value": "tara"
                                                },
                                                {
                                                                "name": "tatuagem",
                                                                "value": "tatuagem"
                                                },
                                                {
                                                                "name": "teacher",
                                                                "value": "teacher"
                                                },
                                                {
                                                                "name": "teacher-student",
                                                                "value": "teacher-student"
                                                },
                                                {
                                                                "name": "Teen",
                                                                "value": "teen"
                                                },
                                                {
                                                                "name": "Teen Titans",
                                                                "value": "teen-titans"
                                                },
                                                {
                                                                "name": "Tentacles",
                                                                "value": "tentacles"
                                                },
                                                {
                                                                "name": "The Collar",
                                                                "value": "the-collar"
                                                },
                                                {
                                                                "name": "The House",
                                                                "value": "the-house"
                                                },
                                                {
                                                                "name": "The Simpsons",
                                                                "value": "the-simpsons"
                                                },
                                                {
                                                                "name": "threesome",
                                                                "value": "threesome"
                                                },
                                                {
                                                                "name": "tia",
                                                                "value": "tia"
                                                },
                                                {
                                                                "name": "Titfuck",
                                                                "value": "titfuck"
                                                },
                                                {
                                                                "name": "tits fuck",
                                                                "value": "tits-fuck"
                                                },
                                                {
                                                                "name": "titsjob",
                                                                "value": "titsjob"
                                                },
                                                {
                                                                "name": "Tomb Raider",
                                                                "value": "tomb-raider"
                                                },
                                                {
                                                                "name": "tomboy",
                                                                "value": "tomboy"
                                                },
                                                {
                                                                "name": "tomgirl",
                                                                "value": "tomgirl"
                                                },
                                                {
                                                                "name": "tracy scops",
                                                                "value": "tracy-scops"
                                                },
                                                {
                                                                "name": "traduzidas",
                                                                "value": "traduzidas"
                                                },
                                                {
                                                                "name": "traindo",
                                                                "value": "traindo"
                                                },
                                                {
                                                                "name": "traição",
                                                                "value": "traicao"
                                                },
                                                {
                                                                "name": "Transformation",
                                                                "value": "transformation"
                                                },
                                                {
                                                                "name": "Travesti",
                                                                "value": "travesti"
                                                },
                                                {
                                                                "name": "trio",
                                                                "value": "trio"
                                                },
                                                {
                                                                "name": "Tsunade",
                                                                "value": "tsunade"
                                                },
                                                {
                                                                "name": "tufos",
                                                                "value": "tufos"
                                                },
                                                {
                                                                "name": "Turma do Chaves",
                                                                "value": "turma-do-chaves"
                                                },
                                                {
                                                                "name": "Um tesao de priminha",
                                                                "value": "um-tesao-de-priminha"
                                                },
                                                {
                                                                "name": "Uncensored",
                                                                "value": "uncensored"
                                                },
                                                {
                                                                "name": "unusual pupils",
                                                                "value": "unusual-pupils"
                                                },
                                                {
                                                                "name": "vadia",
                                                                "value": "vadia"
                                                },
                                                {
                                                                "name": "vagabunda",
                                                                "value": "vagabunda"
                                                },
                                                {
                                                                "name": "vcpvip",
                                                                "value": "vcpvip"
                                                },
                                                {
                                                                "name": "velha",
                                                                "value": "velha"
                                                },
                                                {
                                                                "name": "velho",
                                                                "value": "velho"
                                                },
                                                {
                                                                "name": "videl",
                                                                "value": "videl"
                                                },
                                                {
                                                                "name": "violencia",
                                                                "value": "violencia"
                                                },
                                                {
                                                                "name": "virgin",
                                                                "value": "virgin"
                                                },
                                                {
                                                                "name": "voyeur",
                                                                "value": "voyeur"
                                                },
                                                {
                                                                "name": "western",
                                                                "value": "western"
                                                },
                                                {
                                                                "name": "witchking00",
                                                                "value": "witchking00"
                                                },
                                                {
                                                                "name": "X-Men",
                                                                "value": "x-men"
                                                },
                                                {
                                                                "name": "x-ray",
                                                                "value": "x-ray"
                                                },
                                                {
                                                                "name": "yaoi",
                                                                "value": "yaoi"
                                                },
                                                {
                                                                "name": "young",
                                                                "value": "young"
                                                },
                                                {
                                                                "name": "Young justice",
                                                                "value": "young-justice"
                                                },
                                                {
                                                                "name": "yuri",
                                                                "value": "yuri"
                                                },
                                                {
                                                                "name": "zombie",
                                                                "value": "zombie"
                                                },
                                                {
                                                                "name": "óculos",
                                                                "value": "oculos"
                                                }
                                ],
                                "default": "ben-10"
                }
]
        return [SourceFilter(**item) for item in data]

    name = 'revistasequadrinhos_pt'
    display_name = 'Revistas e Quadrinhos'
    base_url = 'https://revistasequadrinhos.com'
    language = 'pt'
    requests_per_minute = 60


SOURCE = GeneratedGenericSource
