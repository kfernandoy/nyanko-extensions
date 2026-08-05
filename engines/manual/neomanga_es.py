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


"""Adaptador de NeoManga: el catalogo viaja en el payload RSC de Next.js."""

_NEOMANGA_GENRES = (
    "Acción", "Aventura", "Comedia", "Drama", "Fantasía", "Romance", "Ciencia Ficción",
    "Sobrenatural", "Artes Marciales", "Histórico", "Horror", "Misterio", "Psicológico",
    "Slice of Life", "Deportes", "Isekai", "Murim", "Reencarnación", "Cultivación",
)
_NEOMANGA_STATUS = {
    "en_emision": "ongoing", "finalizado": "completed", "pausado": "hiatus",
}


def _neomanga_object(text: str, key: str) -> dict | None:
    """Busca el objeto JSON mas cercano que contenga `key`."""
    needle = f'"{key}"'
    decoder = json.JSONDecoder()
    index = text.find(needle)
    while index != -1:
        start, attempts = text.rfind("{", 0, index), 0
        while start != -1 and attempts < 200:
            try:
                value, _ = decoder.raw_decode(text, start)
            except ValueError:
                pass
            else:
                if isinstance(value, dict) and key in value:
                    return value
            start, attempts = text.rfind("{", 0, start), attempts + 1
        index = text.find(needle, index + 1)
    return None


def _neomanga_payload(text: str, key: str) -> dict | None:
    # El flight de Next.js a veces llega escapado dentro de una cadena.
    for candidate in (text, text.replace('\\"', '"')):
        found = _neomanga_object(candidate, key)
        if found is not None:
            return found
    return None


class NeoMangaSource(MadaraSource):
    """No hay endpoint de busqueda: el catalogo entero se filtra en el cliente."""

    supports_latest = False

    def get_filters(self) -> list[SourceFilter]:
        return [
            SourceFilter("status", "Estado", "select", [
                ("all", "Todos"), ("en_emision", "En emisión"),
                ("finalizado", "Finalizado"), ("pausado", "Pausado"),
            ], "all"),
            SourceFilter("genre", "Género", "select", [("", "Todos")] + [
                (value, value) for value in _NEOMANGA_GENRES
            ], ""),
        ]

    async def browse(self, kind: str, page: int = 1):
        if kind != "popular":
            return {"items": [], "has_more": False}
        return {"items": [self._series(item) for item in await self._catalog()], "has_more": False}

    async def search(self, query: str, page: int = 1, filters: dict | None = None):
        values = filters or {}
        items = await self._catalog()
        needle = query.strip().casefold()
        if needle:
            items = [item for item in items if needle in str(item.get("title") or "").casefold()]
        status = str(values.get("status") or "all")
        if status != "all":
            items = [item for item in items if item.get("status") == status]
        genre = str(values.get("genre") or "")
        if genre:
            items = [item for item in items if genre in (item.get("genres") or [])]
        return {"items": [self._series(item) for item in items], "has_more": False}

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request("GET", f"{self.base_url}/manga/{series_id}")
        response.raise_for_status()
        root = _parse_html(response.text)
        base = str(response.url) or self.base_url
        heading = _first(root, lambda node: node.tag == "h1")
        if heading is None:
            raise SourceNotFoundError(f"{self.display_name}: ficha sin titulo")
        summary = _first(root, lambda node: node.has_class("whitespace-pre-line"))
        cover = next(
            (
                node
                for holder in root.descendants("div")
                if "aspect-[3/4]" in holder.attrs.get("class", "").split()
                for node in holder.descendants("img")
            ),
            None,
        )
        badge = _first(
            root,
            lambda node: node.tag == "span"
            and (node.has_class("bg-success") or node.has_class("bg-danger") or node.has_class("bg-secondary")),
        )
        text = badge.text().casefold() if badge is not None else ""
        return SourceSeries(
            source_id=series_id,
            title=heading.text().strip(),
            source_name=self.name,
            cover_url=urljoin(base, cover.attrs.get("src", "")) if cover is not None else None,
            description=(summary.text().strip() if summary is not None else None) or None,
            status=next(
                (
                    value
                    for word, value in (
                        ("emisión", "ongoing"), ("finalizado", "completed"), ("pausado", "hiatus"),
                    )
                    if word in text
                ),
                None,
            ),
            content_tags=tuple(
                value
                for node in root.descendants("span")
                if node.has_class("bg-accent-soft") and (value := node.text().strip())
            ),
            web_url=f"{self.base_url}/manga/{series_id}",
        )

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request(
            "GET", f"{self.base_url}/manga/{series_id}", headers={"RSC": "1"},
        )
        response.raise_for_status()
        payload = _neomanga_payload(response.text, "chapters") or {}
        result: list[SourceChapter] = []
        for item in payload.get("chapters") or []:
            if not isinstance(item, dict):
                continue
            number = float(item.get("chapter_number") or 0)
            label = str(number)
            label = label[:-2] if label.endswith(".0") else label
            result.append(
                SourceChapter(
                    source_id=f"manga/{series_id}/capitulo/{label}",
                    title=str(item.get("title") or f"Capítulo {label}"),
                    series_id=series_id,
                    source_name=self.name,
                    number=number,
                    language=self.language,
                    uploaded_at=self._date(item.get("published_at")),
                )
            )
        result.sort(key=lambda chapter: chapter.number or 0.0, reverse=True)
        return result

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else str(chapter)
        response = await self._request(
            "GET", urljoin(f"{self.base_url}/", chapter_id.lstrip("/")), headers={"RSC": "1"},
        )
        response.raise_for_status()
        payload = _neomanga_payload(response.text, "chapter") or {}
        sources = ((payload.get("chapter") or {}).get("pages_urls")) or []
        if not sources:
            raise SourceNotFoundError("No se encontraron páginas")
        urls: list[str] = []
        for value in sources:
            text = str(value)
            if not text.startswith("MANGADEX:"):
                urls.append(text)
                continue
            # Las paginas de MangaDex se sirven a traves de un proxy propio.
            identifier = text[len("MANGADEX:"):]
            proxied = await self._request(
                "GET", f"{self.base_url}/api/mangadex-pages/{identifier}",
            )
            proxied.raise_for_status()
            count = len((proxied.json() or {}).get("pages") or [])
            urls.extend(
                f"{self.base_url}/api/manga-page/{identifier}/{index}" for index in range(count)
            )
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

    async def _catalog(self) -> list[dict]:
        response = await self._request(
            "GET", f"{self.base_url}/series", headers={"RSC": "1"},
        )
        response.raise_for_status()
        payload = _neomanga_payload(response.text, "initialMangas") or {}
        return [item for item in payload.get("initialMangas") or [] if isinstance(item, dict)]

    def _series(self, item: dict) -> SourceSeries:
        return SourceSeries(
            source_id=str(item.get("slug") or ""),
            title=str(item.get("title") or ""),
            source_name=self.name,
            cover_url=self._cover(item.get("cover_image_url")),
            description=str(item.get("synopsis") or "") or None,
            status=_NEOMANGA_STATUS.get(str(item.get("status") or "")),
            content_tags=tuple(str(value) for value in item.get("genres") or []),
            web_url=f"{self.base_url}/manga/{item.get('slug')}",
        )

    def _cover(self, value: Any) -> str | None:
        text = str(value or "")
        if not text:
            return None
        if "/_next/image" in text or text.startswith("/"):
            return text
        from urllib.parse import quote

        return f"{self.base_url}/_next/image?url={quote(text, safe='')}&w=640&q=75"

    @staticmethod
    def _date(value: Any) -> str | None:
        from datetime import datetime

        if not value:
            return None
        text = str(value).split(".")[0].split("+")[0].rstrip("Z")
        try:
            return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S").isoformat()
        except ValueError:
            return None


class GeneratedNeoMangaSource(NeoMangaSource):
    name = 'neomanga_es'
    display_name = 'NeoManga'
    base_url = 'https://www.neomanga.online'
    language = 'es'
    requests_per_minute = 60
    content_warning = 'safe'
    image_headers = {'Referer': 'https://www.neomanga.online/'}


SOURCE = GeneratedNeoMangaSource
