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

"""Implementación común del tema MangaThemesia para Nyanko Source v4."""

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
        SourcePage,
        SourcePageContent,
        SourceFilter,
    SourcePreference,
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

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
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
        path = f"{self.manga_directory.rstrip('/')}/"
        params = {"title": query.strip(), "page": "1"}
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
        return (await self._listing(params, path=path))[:limit]

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
            result.append(SourceSeries(source_id=source_id, title=title, source_name=self.name))
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
            result.append(
                SourceChapter(
                    source_id=source_id,
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

class GeneratedMangaThemesiaSource(MangaThemesiaSource):

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

    name = 'luvyaa_id'
    display_name = 'Luvyaa'
    base_url = 'https://v4.luvyaa.co'
    language = 'id'
    manga_directory = '/manga'
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


SOURCE = GeneratedMangaThemesiaSource
