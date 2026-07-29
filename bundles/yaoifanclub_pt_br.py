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
            result.append(SourceSeries(source_id=source_id, title=title, source_name=self.name))
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
                result.append(SourceSeries(source_id=source_id, title=title, source_name=self.name))
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

"""Implementación común de sitios Blogger ZeistManga para Nyanko Source v3."""

import json
import re
from urllib.parse import unquote, urljoin

try:
    from .madara import (
        MadaraSource,
        SourceChapter,
        SourcePage,
        SourceSeries,
        _first,
        _image_url,
        _parse_html,
    )
except ImportError:
    pass


class ZeistMangaSource(MadaraSource):
    manga_category = "Series"
    chapter_category = "Chapter"
    use_new_chapter_feed = False
    chapter_feed_profile = "default"
    popular_is_latest = False
    popular_profile = "default"
    request_referer = ""
    search_profile = "default"
    chapter_profile = "default"
    chapter_categories: tuple[str, ...] = ()
    use_old_chapter_feed = False
    pages_profile = "default"
    latest_order = "published"
    strip_series_query = False

    def __init__(self, fetcher=None) -> None:
        super().__init__(fetcher)
        if self.request_referer:
            self.capabilities.headers["Referer"] = self.request_referer

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        if self.search_profile == "hanmokku":
            response = await self._request(
                "GET",
                f"{self.base_url}/search",
                params={"q": query.strip(), "max-results": 20},
            )
            response.raise_for_status()
            root = _parse_html(response.text)
            return [
                SourceSeries(
                    source_id=urljoin(str(response.url), anchor.attrs["href"]),
                    title=anchor.text().strip(),
                    source_name=self.name,
                )
                for anchor in root.descendants("a")
                if anchor.has_class("ck")
                and anchor.attrs.get("href")
                and anchor.text().strip()
            ][:limit]
        response = await self._feed(
            self.manga_category,
            params={"q": f"label:{self.manga_category} {query.strip()}", "max-results": 21},
        )
        return self._series_from_feed(response.json())[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind == "popular":
            if self.popular_is_latest:
                return await self.browse("latest", page)
            if self.popular_profile == "serieslist" and page > 1:
                response = await self._feed(
                    self.manga_category,
                    params={"max-results": 21, "start-index": 20 * (page - 1) + 1},
                )
                return self._series_from_feed(response.json())[:20]
            if page != 1:
                return []
            response = await self._request("GET", self.base_url)
            response.raise_for_status()
            return self._popular(response.text, str(response.url))
        if kind != "latest":
            return []
        response = await self._feed(
            self.manga_category,
            params={
                "orderby": self.latest_order,
                "max-results": 21,
                "start-index": 20 * (page - 1) + 1,
            },
        )
        return self._series_from_feed(response.json())[:20]

    def _popular(self, html: str, response_url: str) -> list[SourceSeries]:
        root = _parse_html(html)
        if self.popular_profile != "default":
            containers = [
                node
                for node in root.descendants()
                if (
                    self.popular_profile == "pop_card"
                    and node.tag == "div"
                    and node.has_class("pop-card")
                    or self.popular_profile == "serieslist"
                    and node.tag == "li"
                    and self._has_ancestor_class(node, "serieslist")
                    or self.popular_profile == "gallery"
                    and node.tag == "li"
                    and node.has_class("bg")
                    and self._has_ancestor_class(node, "gallery")
                )
            ]
            result: list[SourceSeries] = []
            for container in containers:
                anchor = _first(
                    container,
                    lambda item: item.tag == "a"
                    and bool(item.attrs.get("href"))
                    and bool(item.text().strip()),
                )
                if anchor is None:
                    continue
                result.append(
                    SourceSeries(
                        source_id=urljoin(response_url, anchor.attrs["href"]),
                        title=anchor.text().strip() or "Manga",
                        source_name=self.name,
                    )
                )
            return result
        result: list[SourceSeries] = []
        seen: set[str] = set()
        for node in root.descendants():
            if not (
                self._has_ancestor_class(node, "PopularPosts")
                or self._has_ancestor_id_contains(node, "PopularPosts")
            ):
                continue
            anchor = node if node.tag == "a" and node.attrs.get("href") else None
            if anchor is not None and anchor.text().strip() and anchor.attrs["href"] not in seen:
                href = anchor.attrs["href"].split("?", 1)[0] if self.strip_series_query else anchor.attrs["href"]
                seen.add(href)
                result.append(
                    SourceSeries(
                        source_id=urljoin(response_url, href),
                        title=anchor.text().strip(),
                        source_name=self.name,
                    )
                )
        return result

    def _series_from_feed(self, payload: dict) -> list[SourceSeries]:
        result: list[SourceSeries] = []
        for entry in (payload.get("feed") or {}).get("entry") or []:
            categories = {item.get("term") for item in entry.get("category") or []}
            if self.manga_category not in categories or "Anime" in categories:
                continue
            link = next(
                (item.get("href") for item in entry.get("link") or [] if item.get("rel") == "alternate"),
                "",
            )
            title = (entry.get("title") or {}).get("$t", "")
            if link and title:
                result.append(SourceSeries(source_id=link, title=title, source_name=self.name))
        return result

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        response = await self._request("GET", series_id)
        response.raise_for_status()
        if self.chapter_profile == "html_list":
            return self._html_chapters(response.text, series_id, str(response.url))
        if self.use_old_chapter_feed:
            root = _parse_html(response.text)
            script = next(
                (
                    node
                    for node in root.descendants("script")
                    if node.attrs.get("src") and self._has_ancestor_id_contains(node, "myUL")
                ),
                None,
            )
            if script is None:
                raise ValueError("No se encontró el feed antiguo de capítulos")
            chapter_response = await self._request(
                "GET",
                urljoin(self.base_url, script.attrs["src"].split("?", 1)[0]),
                params={"alt": "json"},
            )
            chapter_response.raise_for_status()
        else:
            category, feed = self._chapter_feed(response.text)
            chapter_response = await self._feed(
                category,
                suffix=feed,
                params={"start-index": 1, "max-results": 999999},
            )
        result: list[SourceChapter] = []
        for entry in (chapter_response.json().get("feed") or {}).get("entry") or []:
            categories = {item.get("term") for item in entry.get("category") or []}
            expected = set(self.chapter_categories or (self.chapter_category,))
            if not categories & expected:
                continue
            link = next(
                (item.get("href") for item in entry.get("link") or [] if item.get("rel") == "alternate"),
                "",
            )
            title = (entry.get("title") or {}).get("$t", "")
            match = re.search(r"(\d+(?:\.\d+)?)", title)
            if self.chapter_profile == "yokai" and title.lower().startswith("chapter"):
                title = f"الفصل {title[7:].strip()}"
            result.append(
                SourceChapter(
                    source_id=link,
                    title=title or "Capítulo",
                    series_id=series_id,
                    source_name=self.name,
                    number=float(match.group(1)) if match else None,
                    uploaded_at=(entry.get("published") or entry.get("updated") or {}).get("$t"),
                )
            )
        if self.chapter_profile == "number_desc":
            result.sort(key=lambda chapter: chapter.number or -1, reverse=True)
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        response = await self._request("GET", chapter_id)
        response.raise_for_status()
        root = _parse_html(response.text)
        if self.pages_profile == "textarea_raw":
            textarea = next(
                (node for node in root.descendants("textarea") if node.attrs.get("id") == "zeist-raw-data"),
                None,
            )
            root = _parse_html(textarea.text() if textarea else "")
        elif self.pages_profile == "template_html":
            match = re.search(r"const\s+content\s*=\s*`(.*?)`;", response.text, re.S)
            root = _parse_html(match.group(1) if match else "")
        elif self.pages_profile == "json_array":
            match = re.search(r"=\s*(\[[^\]]+\])", response.text, re.S)
            urls = json.loads(match.group(1)) if match else []
            return self._source_pages(urls, chapter_id)
        elif self.pages_profile == "ulas_script":
            script = response.text.partition("config['chapterImage']")[2]
            urls = re.findall(r'"(https?://[^"]+)"', script)
            if urls:
                return self._source_pages(urls, chapter_id)
        if self.pages_profile == "separator_links":
            urls = [
                urljoin(str(response.url), node.attrs["href"])
                for node in root.descendants("a")
                if node.attrs.get("href") and self._has_ancestor_class(node, "separator")
            ]
            return self._source_pages(urls, chapter_id)
        urls = [
            _image_url(image, str(response.url))
            for image in root.descendants("img")
            if (
                self.pages_profile == "broad_separators"
                and self._has_ancestor_class(image, "separator")
                or self.pages_profile == "article_images"
                and self._has_ancestor_class(image, "post")
                or self._has_ancestor_class(image, "separator")
                and self._has_ancestor_class(image, "check-box")
                or self._has_ancestor_id_contains(image, "reader")
            )
        ]
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
            for index, url in enumerate(dict.fromkeys(url for url in urls if url), 1)
        ]

    def _html_chapters(self, html: str, series_id: str, response_url: str) -> list[SourceChapter]:
        root = _parse_html(html)
        result: list[SourceChapter] = []
        for node in root.descendants("div"):
            if not node.has_class("flexch-infoz") or not self._has_ancestor_class(node, "series-chapterlist"):
                continue
            anchor = _first(node, lambda item: item.tag == "a" and bool(item.attrs.get("href")))
            if anchor is None:
                continue
            title_node = _first(node, lambda item: item.tag == "span" and bool(item.text().strip()))
            title = title_node.text().strip() if title_node else anchor.text().strip() or "Capítulo"
            match = re.search(r"(\d+(?:\.\d+)?)", title)
            result.append(
                SourceChapter(
                    source_id=urljoin(response_url, anchor.attrs["href"]),
                    title=title,
                    series_id=series_id,
                    source_name=self.name,
                    number=float(match.group(1)) if match else None,
                )
            )
        return result

    async def _feed(self, category: str, *, suffix: str = "", params: dict | None = None):
        path = f"{self.base_url}/feeds/posts/default/-/{category}"
        if suffix:
            path += f"/{suffix.strip('/')}"
        response = await self._request("GET", path, params={"alt": "json", **(params or {})})
        response.raise_for_status()
        return response

    def _chapter_feed(self, html: str) -> tuple[str, str]:
        if self.chapter_feed_profile == "comicverse":
            root = _parse_html(html)
            label = next(
                (
                    node.attrs["data-label"]
                    for node in root.descendants("div")
                    if node.has_class("manga-widget") and node.attrs.get("data-label")
                ),
                "",
            )
            if not label:
                raise ValueError("No se encontró el feed de capítulos")
            return self.chapter_category, label
        if self.chapter_feed_profile in {"data_label", "og_title", "title", "cat_name"}:
            root = _parse_html(html)
            if self.chapter_feed_profile == "data_label":
                node = next(
                    (
                        item
                        for item in root.descendants()
                        if item.has_class("chapter_get") and item.attrs.get("data-labelchapter")
                    ),
                    None,
                )
                return (node.attrs["data-labelchapter"], "") if node else self._missing_feed()
            if self.chapter_feed_profile == "og_title":
                node = next(
                    (
                        item
                        for item in root.descendants("meta")
                        if item.attrs.get("property") == "og:title" and item.attrs.get("content")
                    ),
                    None,
                )
                if node:
                    return self.chapter_category, node.attrs["content"]
            if self.chapter_feed_profile == "title":
                node = next(
                    (item for item in root.descendants("h1") if item.has_class("entry-title")),
                    None,
                )
                return (node.text().strip(), "") if node else self._missing_feed()
            if self.chapter_feed_profile == "cat_name":
                match = re.search(r"catNameProject.*?=\s+?\('([^']+)", html, re.S)
                return (self.chapter_category, match.group(1)) if match else self._missing_feed()

        match = None if self.use_new_chapter_feed else re.search(
            r"""clwd\.run\(["'](.*?)["']\)""",
            html,
        )
        category, suffix = (
            (self.chapter_category, match.group(1))
            if match
            else (self._new_feed(html), "")
        )
        if self.chapter_feed_profile == "yurimoon":
            category = re.sub(r"\s{2,}", "", re.sub(r"[\u0600-\u06ff]", "", unquote(category)))
            suffix = re.sub(r"\s{2,}", "", re.sub(r"[\u0600-\u06ff]", "", unquote(suffix)))
        return category, suffix

    @staticmethod
    def _missing_feed():
        raise ValueError("No se encontró el feed de capítulos")

    @staticmethod
    def _new_feed(html: str) -> str:
        match = re.search(r"""label\s*=\s*'([^']+)'""", html)
        if match is None:
            raise ValueError("No se encontró el feed de capítulos")
        return match.group(1)

    @staticmethod
    def _has_ancestor_class(node: object, class_name: str) -> bool:
        parent = getattr(node, "parent", None)
        while parent is not None:
            if parent.has_class(class_name):
                return True
            parent = parent.parent
        return False

    @staticmethod
    def _has_ancestor_id_contains(node: object, value: str) -> bool:
        parent = getattr(node, "parent", None)
        while parent is not None:
            if value.lower() in parent.attrs.get("id", "").lower():
                return True
            parent = parent.parent
        return False

class GeneratedZeistMangaSource(ZeistMangaSource):
    name = 'yaoifanclub_pt_br'
    display_name = 'Yaoi Fan Club'
    base_url = 'https://www.yaoifanclub.com'
    language = 'pt-BR'
    manga_category = 'Series'
    chapter_category = 'Chapter'
    use_new_chapter_feed = True
    chapter_feed_profile = 'default'
    popular_is_latest = False
    popular_profile = 'default'
    request_referer = 'https://www.blogger.com/blogin.g?blogspotURL=https://www.yaoifanclub.com/&type=blog&bpli=1'
    search_profile = 'default'
    chapter_profile = 'default'
    chapter_categories = ()
    use_old_chapter_feed = False
    pages_profile = 'default'
    latest_order = 'published'
    strip_series_query = False


SOURCE = GeneratedZeistMangaSource
