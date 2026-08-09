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


"""Adaptador de YellowNote (XChina): albumes de fotos paginados."""

_YELLOWNOTE_STRINGS = {
    'en': {
        'filter.sort.title': 'Sort by',
        'filter.sort.option.last-update': 'Last Update',
        'filter.sort.option.popularity': 'Popularity',
        'filter.sort.option.most-comments': 'Comment Count',
        'filter.sort.option.latest-comments': 'Latest Comments',
        'filter.header.ignored-when-search': 'These filters will be ignored when text search is active!',
        'filter.category.title': 'Category',
        'filter.category.option.theme.xiuren-featured': 'Theme: Xiuren Featured',
        'filter.category.option.theme.large-scale': 'Theme: Large Scale',
        'filter.category.option.theme.sex': 'Theme: Sex',
        'filter.category.option.theme.exposure': 'Theme: Exposure',
        'filter.category.option.theme.cosplay': 'Theme: Cosplay',
        'filter.category.option.theme.sex-toy': 'Theme: Sex Toy',
        'filter.category.option.theme.bondage': 'Theme: Bondage',
        'filter.category.option.theme.shaved-pussy': 'Theme: Shaved Pussy',
        'filter.category.option.theme.lesbian': 'Theme: Lesbian',
        'filter.category.option.theme.with-original-photos': 'Theme: With Original Photos',
        'filter.category.option.theme.with-video': 'Theme: With Video(s)',
        'filter.category.option.theme.amateur': 'Theme: Amateur',
        'filter.category.option.chinese-studios-pans': 'Chinese Studios: PANS',
        'filter.category.option.chinese-studios-wind-sings': 'Chinese Studios: Wind sings',
        'filter.category.option.chinese-studios-xing-se': 'Chinese Studios: Xing Se',
        'filter.category.option.chinese-studios-huang-fu': 'Chinese Studios: Huang Fu',
        'filter.category.option.chinese-studios-other-studios': 'Chinese Studios: Other Studios',
        'filter.category.option.chinese-studios-metcn': 'Chinese Studios: MetCN',
        'filter.category.option.chinese-studios-litu': 'Chinese Studios: Litu',
        'filter.category.option.chinese-studios-midnight-project': 'Chinese Studios: Midnight Project',
        'filter.category.option.chinese-studios-pandora': 'Chinese Studios: Pandora',
        'filter.category.option.chinese-studios-missleg': 'Chinese Studios: MISSLEG',
        'filter.category.option.chinese-studios-iss': 'Chinese Studios: ISS',
        'filter.category.option.chinese-studios-aiss': 'Chinese Studios: AISS',
        'filter.category.option.chinese-studios-au': 'Chinese Studios: AU',
        'filter.category.option.chinese-studios-beijing-angel': 'Chinese Studios: Beijing Angel',
        'filter.category.option.chinese-studios-wuji-works': 'Chinese Studios: Wuji Works',
        'filter.category.option.chinese-studios-pomelo': 'Chinese Studios: Pomelo',
        'filter.category.option.chinese-studios-sk-silk': 'Chinese Studios: SK Silk',
        'filter.category.option.chinese-studios-ddy': 'Chinese Studios: DDY',
        'filter.category.option.chinese-studios-dongguan-vgirls': 'Chinese Studios: Dongguan VGirls',
        'filter.category.option.chinese-studios-youmei': 'Chinese Studios: YouMei',
        'filter.category.option.other-photos-chinese-nude': 'Other Photos: Chinese Nude',
        'filter.category.option.other-photos-korean-nude': 'Other Photos: Korean Nude',
        'filter.category.option.other-photos-taiwan-nude': 'Other Photos: Taiwan Nude',
        'filter.category.option.other-photos-other-regions': 'Other Photos: Other Regions',
        'filter.category.option.xiuren-all': 'Xiuren: All',
        'filter.category.option.xiuren-leaked': 'Xiuren: Leaked',
        'filter.category.option.xiuren-huayang': 'Xiuren: HuaYang',
        'filter.category.option.xiuren-mygirl': 'Xiuren: MyGirl',
        'filter.category.option.xiuren-imiss': 'Xiuren: IMiss',
        'filter.category.option.xiuren-miitao': 'Xiuren: MiiTao',
        'filter.category.option.xiuren-feilin': 'Xiuren: FEILIN',
        'filter.category.option.xiuren-youwu': 'Xiuren: YouWu',
        'filter.category.option.xiuren-wings': 'Xiuren: WingS',
        'filter.category.option.xiuren-ruisg': 'Xiuren: RUISG',
        'filter.category.option.korean-studios-makemodel': 'Korean Studios: Makemodel',
        'filter.category.option.korean-studios-pure-media': 'Korean Studios: Pure Media',
        'filter.category.option.korean-studios-espacia-korea': 'Korean Studios: Espacia Korea',
        'filter.category.option.korean-studios-loozy': 'Korean Studios: Loozy',
        'filter.category.option.japanese-studios-graphis': 'Japanese Studios: Graphis',
        'filter.category.option.japanese-studios-kuni-scan': 'Japanese Studios: KUNI Scan',
        'filter.category.option.japanese-studios-weekly-post-digital-photo': 'Japanese Studios: Weekly Post-Digital Photo',
        'filter.category.option.japanese-studios-morning-sexy': 'Japanese Studios: Morning SEXY',
        'filter.category.option.japanese-studios-prestige': 'Japanese Studios: Prestige',
        'filter.category.option.japanese-studios-x-city': 'Japanese Studios: X-City',
        'filter.category.option.japanese-studios-friday': 'Japanese Studios: FRIDAY',
        'filter.category.option.japanese-studios-super-pose-book': 'Japanese Studios: Super Pose Book',
        'filter.category.option.japanese-studios-urabon': 'Japanese Studios: Urabon',
        'filter.category.option.japanese-studios-escape': 'Japanese Studios: Escape',
        'filter.category.option.japanese-studios-flash': 'Japanese Studios: Flash',
        'filter.category.option.taiwan-studios-jvid': 'Taiwan Studios: JVID',
        'filter.category.option.taiwan-studios-fantasy-factory': 'Taiwan Studios: Fantasy Factory',
        'filter.category.option.taiwan-studios-tpimage': 'Taiwan Studios: TPimage',
        'filter.category.option.others-ai-photos': 'Others: AI Photos',
        'config.image_quality.title': 'Image Quality',
        'config.image_quality.summary': 'Select image quality. Original (JPG) has best quality, HD (WebP) loads faster.',
    },
    'es': {
        'filter.sort.title': 'Ordenar por',
        'filter.sort.option.last-update': 'Última actualización',
        'filter.sort.option.popularity': 'Contenido más popular',
        'filter.sort.option.most-comments': 'Más comentarios',
        'filter.sort.option.latest-comments': 'Comentarios más recientes',
        'filter.header.ignored-when-search': '¡Estos filtros se ignorarán cuando la búsqueda de texto esté activa!',
        'filter.category.title': 'Categoría del álbum',
        'filter.category.option.theme.xiuren-featured': 'Xiuren Gran escala',
        'filter.category.option.theme.large-scale': 'Gran Escala',
        'filter.category.option.theme.sex': 'Sexo',
        'filter.category.option.theme.exposure': 'Exposición',
        'filter.category.option.theme.cosplay': 'Cosplay',
        'filter.category.option.theme.sex-toy': 'Juguete Sexual',
        'filter.category.option.theme.bondage': 'Esclavitud',
        'filter.category.option.theme.shaved-pussy': 'Coño Afeitado',
        'filter.category.option.theme.lesbian': 'Lesbiana',
        'filter.category.option.theme.with-original-photos': 'Con foto original',
        'filter.category.option.theme.with-video': 'Con Vídeo(s)',
        'filter.category.option.theme.amateur': 'Aficionada',
        'filter.category.option.chinese-studios-pans': 'Estudios chinos: PANS',
        'filter.category.option.chinese-studios-wind-sings': 'Estudios chinos: El viento canta',
        'filter.category.option.chinese-studios-xing-se': 'Estudios chinos: Xing Se',
        'filter.category.option.chinese-studios-huang-fu': 'Estudios chinos: Huang Fu',
        'filter.category.option.chinese-studios-other-studios': 'Estudios chinos: Otros estudios',
        'filter.category.option.chinese-studios-metcn': 'Estudios chinos: MetCN',
        'filter.category.option.chinese-studios-litu': 'Estudios chinos: Litu',
        'filter.category.option.chinese-studios-midnight-project': 'Estudios chinos: Proyecto de medianoche',
        'filter.category.option.chinese-studios-pandora': 'Estudios chinos: Pandora',
        'filter.category.option.chinese-studios-missleg': 'Estudios chinos: MISSLEG',
        'filter.category.option.chinese-studios-iss': 'Estudios chinos: ISS',
        'filter.category.option.chinese-studios-aiss': 'Estudios chinos: AISS',
        'filter.category.option.chinese-studios-au': 'Estudios chinos: AU',
        'filter.category.option.chinese-studios-beijing-angel': 'Estudios chinos: Beijing Angel',
        'filter.category.option.chinese-studios-wuji-works': 'Estudios chinos: Wuji Works',
        'filter.category.option.chinese-studios-pomelo': 'Estudios chinos: Pomelo',
        'filter.category.option.chinese-studios-sk-silk': 'Estudios chinos: SK Silk',
        'filter.category.option.chinese-studios-ddy': 'Estudios chinos: DDY',
        'filter.category.option.chinese-studios-dongguan-vgirls': 'Estudios chinos: Dongguan VGirls',
        'filter.category.option.chinese-studios-youmei': 'Estudios chinos: YouMei',
        'filter.category.option.other-photos-chinese-nude': 'Otras fotos: Desnuda China',
        'filter.category.option.other-photos-korean-nude': 'Otras fotos: Desnudo Coreano',
        'filter.category.option.other-photos-taiwan-nude': 'Otras fotos: Desnuda Taiwan',
        'filter.category.option.other-photos-other-regions': 'Otras fotos: Otras Regiones',
        'filter.category.option.xiuren-all': 'Xiuren: all',
        'filter.category.option.xiuren-leaked': 'Xiuren: Filtrado',
        'filter.category.option.xiuren-huayang': 'Xiuren: HuaYang',
        'filter.category.option.xiuren-mygirl': 'Xiuren: MyGirl',
        'filter.category.option.xiuren-imiss': 'Xiuren: IMiss',
        'filter.category.option.xiuren-miitao': 'Xiuren: MiiTao',
        'filter.category.option.xiuren-feilin': 'Xiuren: FEILIN',
        'filter.category.option.xiuren-youwu': 'Xiuren: YouWu',
        'filter.category.option.xiuren-wings': 'Xiuren: WingS',
        'filter.category.option.xiuren-ruisg': 'Xiuren: RUISG',
        'filter.category.option.korean-studios-makemodel': 'Estudios coreanos: Makemodel',
        'filter.category.option.korean-studios-pure-media': 'Estudios coreanos: Pure Medios',
        'filter.category.option.korean-studios-espacia-korea': 'Estudios coreanos: Espacia Korea',
        'filter.category.option.korean-studios-loozy': 'Estudios coreanos: Loozy',
        'filter.category.option.japanese-studios-graphis': 'Estudios japonesas: Graphis',
        'filter.category.option.japanese-studios-kuni-scan': 'Estudios japonesas: KUNI Scan',
        'filter.category.option.japanese-studios-weekly-post-digital-photo': 'Estudios japonesas: Foto Semanal Post-Digital',
        'filter.category.option.japanese-studios-morning-sexy': 'Estudios japonesas: Matutinos Sexy',
        'filter.category.option.japanese-studios-prestige': 'Estudios japonesas: Prestige',
        'filter.category.option.japanese-studios-x-city': 'Estudios japonesas: X-City',
        'filter.category.option.japanese-studios-friday': 'Estudios japonesas: FRIDAY',
        'filter.category.option.japanese-studios-super-pose-book': 'Estudios japonesas: Super Pose Book',
        'filter.category.option.japanese-studios-urabon': 'Estudios japonesas: Urabon',
        'filter.category.option.japanese-studios-escape': 'Estudios japonesas: Escape',
        'filter.category.option.japanese-studios-flash': 'Estudios japonesas: Flash',
        'filter.category.option.taiwan-studios-jvid': 'Estudios de Taiwán: JVID',
        'filter.category.option.taiwan-studios-fantasy-factory': 'Estudios de Taiwán: Fantasy Factory',
        'filter.category.option.taiwan-studios-tpimage': 'Estudios de Taiwán: TPimage',
        'filter.category.option.others-ai-photos': 'Otras: AI Photos',
        'config.image_quality.title': 'Calidad de Imagen',
        'config.image_quality.summary': 'Seleccione calidad. Original (JPG) mejor calidad, HD (WebP) carga más rápido.',
    },
    'ko': {
        'filter.sort.title': '정렬 방식',
        'filter.sort.option.last-update': '업데이트 날짜',
        'filter.sort.option.popularity': '인기 콘텐츠',
        'filter.sort.option.most-comments': '댓글 많은 순',
        'filter.sort.option.latest-comments': '최신 댓글 순',
        'filter.header.ignored-when-search': '텍스트 검색이 활성화되면 이 필터는 무시됩니다!',
        'filter.category.title': '앨범 분류',
        'filter.category.option.theme.xiuren-featured': 'Xiuren 대판',
        'filter.category.option.theme.large-scale': '대판',
        'filter.category.option.theme.sex': '섹스',
        'filter.category.option.theme.exposure': '노출',
        'filter.category.option.theme.cosplay': 'Cosplay',
        'filter.category.option.theme.sex-toy': '섹스 토이',
        'filter.category.option.theme.bondage': '속박',
        'filter.category.option.theme.shaved-pussy': '면도한 음부',
        'filter.category.option.theme.lesbian': '레즈비언',
        'filter.category.option.theme.with-original-photos': '원본 있음',
        'filter.category.option.theme.with-video': '동영상 포함',
        'filter.category.option.theme.amateur': '아마추어',
        'filter.category.option.chinese-studios-pans': '중국 스튜디오: PANS',
        'filter.category.option.chinese-studios-wind-sings': '중국 스튜디오: 바람이 노래한다',
        'filter.category.option.chinese-studios-xing-se': '중국 스튜디오: 싱 세',
        'filter.category.option.chinese-studios-huang-fu': '중국 스튜디오: 황 푸',
        'filter.category.option.chinese-studios-other-studios': '중국 스튜디오: 다른 스튜디오',
        'filter.category.option.chinese-studios-metcn': '중국 스튜디오: MetCN',
        'filter.category.option.chinese-studios-litu': '중국 스튜디오: Litu',
        'filter.category.option.chinese-studios-midnight-project': '중국 스튜디오: 미드나잇 프로젝트',
        'filter.category.option.chinese-studios-pandora': '중국 스튜디오: Pandora',
        'filter.category.option.chinese-studios-missleg': '중국 스튜디오: MISSLEG',
        'filter.category.option.chinese-studios-iss': '중국 스튜디오: ISS',
        'filter.category.option.chinese-studios-aiss': '중국 스튜디오: AISS',
        'filter.category.option.chinese-studios-au': '중국 스튜디오: AU',
        'filter.category.option.chinese-studios-beijing-angel': '중국 스튜디오: Beijing Angel',
        'filter.category.option.chinese-studios-wuji-works': '중국 스튜디오: Wuji Works',
        'filter.category.option.chinese-studios-pomelo': '중국 스튜디오: 포멜로',
        'filter.category.option.chinese-studios-sk-silk': '중국 스튜디오: SK Silk',
        'filter.category.option.chinese-studios-ddy': '중국 스튜디오: DDY',
        'filter.category.option.chinese-studios-dongguan-vgirls': '중국 스튜디오: Dongguan VGirls',
        'filter.category.option.chinese-studios-youmei': '중국 스튜디오: YouMei',
        'filter.category.option.other-photos-chinese-nude': '다른 사진들: 중국 누드',
        'filter.category.option.other-photos-korean-nude': '다른 사진들: 한국 누드',
        'filter.category.option.other-photos-taiwan-nude': '다른 사진들: 대만 누드',
        'filter.category.option.other-photos-other-regions': '다른 사진들: 기타 지역',
        'filter.category.option.xiuren-all': 'Xiuren: All',
        'filter.category.option.xiuren-leaked': 'Xiuren: 누출됨',
        'filter.category.option.xiuren-huayang': 'Xiuren: HuaYang',
        'filter.category.option.xiuren-mygirl': 'Xiuren: MyGirl',
        'filter.category.option.xiuren-imiss': 'Xiuren: IMiss',
        'filter.category.option.xiuren-miitao': 'Xiuren: MiiTao',
        'filter.category.option.xiuren-feilin': 'Xiuren: FEILIN',
        'filter.category.option.xiuren-youwu': 'Xiuren: YouWu',
        'filter.category.option.xiuren-wings': 'Xiuren: WingS',
        'filter.category.option.xiuren-ruisg': 'Xiuren: RUISG',
        'filter.category.option.korean-studios-makemodel': '한국 스튜디오: Makemodel',
        'filter.category.option.korean-studios-pure-media': '한국 스튜디오: Pure Media',
        'filter.category.option.korean-studios-espacia-korea': '한국 스튜디오: Espacia Korea',
        'filter.category.option.korean-studios-loozy': '한국 스튜디오: Loozy',
        'filter.category.option.japanese-studios-graphis': '일본 스튜디오: Graphis',
        'filter.category.option.japanese-studios-kuni-scan': '일본 스튜디오: KUNI Scan',
        'filter.category.option.japanese-studios-weekly-post-digital-photo': '일본 스튜디오: 주간 포스트 디지털 사진',
        'filter.category.option.japanese-studios-morning-sexy': '일본 스튜디오: 아침 연예 섹시',
        'filter.category.option.japanese-studios-prestige': '일본 스튜디오: Prestige',
        'filter.category.option.japanese-studios-x-city': '일본 스튜디오: X-City',
        'filter.category.option.japanese-studios-friday': '일본 스튜디오: FRIDAY',
        'filter.category.option.japanese-studios-super-pose-book': '일본 스튜디오: Super Pose Book',
        'filter.category.option.japanese-studios-urabon': '일본 스튜디오: Urabon',
        'filter.category.option.japanese-studios-escape': '일본 스튜디오: Escape',
        'filter.category.option.japanese-studios-flash': '일본 스튜디오: Flash',
        'filter.category.option.taiwan-studios-jvid': '대만 스튜디오: JVID',
        'filter.category.option.taiwan-studios-fantasy-factory': '대만 스튜디오: Fantasy Factory',
        'filter.category.option.taiwan-studios-tpimage': '대만 스튜디오: TPimage',
        'filter.category.option.others-ai-photos': '기타: AI 사진',
        'config.image_quality.title': '이미지 품질',
        'config.image_quality.summary': '이미지 품질을 선택하세요. 원본(JPG)이 가장 좋고 HD(WebP)가 더 빠릅니다.',
    },
    'zh-Hans': {
        'filter.sort.title': '排序方式',
        'filter.sort.option.last-update': '更新时间',
        'filter.sort.option.popularity': '最热内容',
        'filter.sort.option.most-comments': '最多评论',
        'filter.sort.option.latest-comments': '最近评论',
        'filter.header.ignored-when-search': '以下过滤在文本搜索时会被忽略！',
        'filter.category.title': '专辑分类',
        'filter.category.option.theme.xiuren-featured': '主题: 秀人网特色',
        'filter.category.option.theme.large-scale': '主题: 大尺度',
        'filter.category.option.theme.sex': '主题: 性爱',
        'filter.category.option.theme.exposure': '主题: 露出',
        'filter.category.option.theme.cosplay': '主题: Cosplay',
        'filter.category.option.theme.sex-toy': '主题: 道具',
        'filter.category.option.theme.bondage': '主题: 捆绑',
        'filter.category.option.theme.shaved-pussy': '主题: 白虎',
        'filter.category.option.theme.lesbian': '主题: 女同',
        'filter.category.option.theme.with-original-photos': '主题: 有原图',
        'filter.category.option.theme.with-video': '主题: 有视频',
        'filter.category.option.theme.amateur': '主题: 业余自拍',
        'filter.category.option.chinese-studios-pans': '中国工作室: PANS',
        'filter.category.option.chinese-studios-wind-sings': '中国工作室: 风吟鸟唱',
        'filter.category.option.chinese-studios-xing-se': '中国工作室: 行色',
        'filter.category.option.chinese-studios-huang-fu': '中国工作室: 黄甫',
        'filter.category.option.chinese-studios-other-studios': '中国工作室: 其他中国工作室',
        'filter.category.option.chinese-studios-metcn': '中国工作室: 相约中国',
        'filter.category.option.chinese-studios-litu': '中国工作室: 丽图',
        'filter.category.option.chinese-studios-midnight-project': '中国工作室: 深夜企划',
        'filter.category.option.chinese-studios-pandora': '中国工作室: 潘多拉',
        'filter.category.option.chinese-studios-missleg': '中国工作室: 蜜丝',
        'filter.category.option.chinese-studios-iss': '中国工作室: ISS系列',
        'filter.category.option.chinese-studios-aiss': '中国工作室: 爱丝',
        'filter.category.option.chinese-studios-au': '中国工作室: AU',
        'filter.category.option.chinese-studios-beijing-angel': '中国工作室: 北京天使',
        'filter.category.option.chinese-studios-wuji-works': '中国工作室: 无忌影社',
        'filter.category.option.chinese-studios-pomelo': '中国工作室: 蜜柚摄影',
        'filter.category.option.chinese-studios-sk-silk': '中国工作室: SK丝库',
        'filter.category.option.chinese-studios-ddy': '中国工作室: DDY',
        'filter.category.option.chinese-studios-dongguan-vgirls': '中国工作室: 东莞V女郎',
        'filter.category.option.chinese-studios-youmei': '中国工作室: 尤美',
        'filter.category.option.other-photos-chinese-nude': '各国其他套图: 国模套图',
        'filter.category.option.other-photos-korean-nude': '各国其他套图: 韩模套图',
        'filter.category.option.other-photos-taiwan-nude': '各国其他套图: 台模套图',
        'filter.category.option.other-photos-other-regions': '各国其他套图: 其他地区套图',
        'filter.category.option.xiuren-all': '秀人网旗下: 全部秀人旗下',
        'filter.category.option.xiuren-leaked': '秀人网旗下: 私购流出',
        'filter.category.option.xiuren-huayang': '秀人网旗下: 花漾',
        'filter.category.option.xiuren-mygirl': '秀人网旗下: 美媛馆',
        'filter.category.option.xiuren-imiss': '秀人网旗下: 爱蜜社',
        'filter.category.option.xiuren-miitao': '秀人网旗下: 蜜桃社',
        'filter.category.option.xiuren-feilin': '秀人网旗下: FEILIN嗲囡囡',
        'filter.category.option.xiuren-youwu': '秀人网旗下: 尤物馆',
        'filter.category.option.xiuren-wings': '秀人网旗下: 影私荟',
        'filter.category.option.xiuren-ruisg': '秀人网旗下: 瑞丝馆',
        'filter.category.option.korean-studios-makemodel': '韩国工作室: Makemodel',
        'filter.category.option.korean-studios-pure-media': '韩国工作室: Pure Media',
        'filter.category.option.korean-studios-espacia-korea': '韩国工作室: Espacia Korea',
        'filter.category.option.korean-studios-loozy': '韩国工作室: Loozy',
        'filter.category.option.japanese-studios-graphis': '日本工作室: Graphis',
        'filter.category.option.japanese-studios-kuni-scan': '日本工作室: KUNI Scan',
        'filter.category.option.japanese-studios-weekly-post-digital-photo': '日本工作室: 周刊ポストデジタル写真集',
        'filter.category.option.japanese-studios-morning-sexy': '日本工作室: アサ芸SEXY',
        'filter.category.option.japanese-studios-prestige': '日本工作室: Prestige',
        'filter.category.option.japanese-studios-x-city': '日本工作室: X-City',
        'filter.category.option.japanese-studios-friday': '日本工作室: FRIDAY',
        'filter.category.option.japanese-studios-super-pose-book': '日本工作室: Super Pose Book',
        'filter.category.option.japanese-studios-urabon': '日本工作室: Urabon',
        'filter.category.option.japanese-studios-escape': '日本工作室: Escape',
        'filter.category.option.japanese-studios-flash': '日本工作室: FLASHデジタル写真集',
        'filter.category.option.taiwan-studios-jvid': '台湾工作室: JVID',
        'filter.category.option.taiwan-studios-fantasy-factory': '台湾工作室: Fantasy Factory',
        'filter.category.option.taiwan-studios-tpimage': '台湾工作室: TPimage',
        'filter.category.option.others-ai-photos': '其他套图: AI图区',
        'config.image_quality.title': '图片质量',
        'config.image_quality.summary': '选择图片质量。原图(JPG)质量最高，高清(WebP)加载更快。',
    },
    'zh-Hant': {
        'filter.sort.title': '排序方式',
        'filter.sort.option.last-update': '更新時間',
        'filter.sort.option.popularity': '最熱門內容',
        'filter.sort.option.most-comments': '最多評論',
        'filter.sort.option.latest-comments': '最新評論',
        'filter.category.title': '專輯分類',
        'filter.category.option.theme.xiuren-featured': '主題: 秀人網特色',
        'filter.category.option.theme.large-scale': '主題: 大尺度',
        'filter.category.option.theme.sex': '主題: 性愛',
        'filter.category.option.theme.exposure': '主題: 露出',
        'filter.category.option.theme.cosplay': '主題: Cosplay',
        'filter.category.option.theme.sex-toy': '主題: 道具',
        'filter.category.option.theme.bondage': '主題: 捆綁',
        'filter.category.option.theme.shaved-pussy': '主題: 白虎',
        'filter.category.option.theme.lesbian': '主題: 女同',
        'filter.category.option.theme.with-original-photos': '主題: 有原圖',
        'filter.category.option.theme.with-video': '主題: 有影片',
        'filter.category.option.theme.amateur': '主題: 業餘自拍',
        'filter.category.option.chinese-studios-pans': '中國工作室: PANS',
        'filter.category.option.chinese-studios-wind-sings': '中國工作室: 風吟鳥唱',
        'filter.category.option.chinese-studios-xing-se': '中國工作室: 行色',
        'filter.category.option.chinese-studios-huang-fu': '中國工作室: 黃甫',
        'filter.category.option.chinese-studios-other-studios': '中國工作室: 其他中國工作室',
        'filter.category.option.chinese-studios-metcn': '中國工作室: 相約中國',
        'filter.category.option.chinese-studios-litu': '中國工作室: 麗圖',
        'filter.category.option.chinese-studios-midnight-project': '中國工作室: 深夜企劃',
        'filter.category.option.chinese-studios-pandora': '中國工作室: 潘多菈',
        'filter.category.option.chinese-studios-missleg': '中國工作室: 蜜絲',
        'filter.category.option.chinese-studios-iss': '中國工作室: ISS係列',
        'filter.category.option.chinese-studios-aiss': '中國工作室: 愛絲',
        'filter.category.option.chinese-studios-au': '中國工作室: AU',
        'filter.category.option.chinese-studios-beijing-angel': '中國工作室: 北京天使',
        'filter.category.option.chinese-studios-wuji-works': '中國工作室: 無忌影社',
        'filter.category.option.chinese-studios-pomelo': '中國工作室: 蜜柚攝影',
        'filter.category.option.chinese-studios-sk-silk': '中國工作室: SK絲庫',
        'filter.category.option.chinese-studios-ddy': '中國工作室: DDY',
        'filter.category.option.chinese-studios-dongguan-vgirls': '中國工作室: 東莞V女郎',
        'filter.category.option.chinese-studios-youmei': '中國工作室: 尤美',
        'filter.category.option.other-photos-chinese-nude': '各國其他套圖: 國模套圖',
        'filter.category.option.other-photos-korean-nude': '各國其他套圖: 韓模套圖',
        'filter.category.option.other-photos-taiwan-nude': '各國其他套圖: 臺模套圖',
        'filter.category.option.other-photos-other-regions': '各國其他套圖: 其他地區套圖',
        'filter.category.option.xiuren-all': '秀人網旗下: 全部秀人旗下',
        'filter.category.option.xiuren-leaked': '秀人網旗下: 私購流出',
        'filter.category.option.xiuren-huayang': '秀人網旗下: 花漾',
        'filter.category.option.xiuren-mygirl': '秀人網旗下: 美媛館',
        'filter.category.option.xiuren-imiss': '秀人網旗下: 愛蜜社',
        'filter.category.option.xiuren-miitao': '秀人網旗下: 蜜桃社',
        'filter.category.option.xiuren-feilin': '秀人網旗下: FEILIN嗲囡囡',
        'filter.category.option.xiuren-youwu': '秀人網旗下: 尤物館',
        'filter.category.option.xiuren-wings': '秀人網旗下: 影私荟',
        'filter.category.option.xiuren-ruisg': '秀人網旗下: 瑞絲館',
        'filter.category.option.korean-studios-makemodel': '韓國工作室: Makemodel',
        'filter.category.option.korean-studios-pure-media': '韓國工作室: Pure Media',
        'filter.category.option.korean-studios-espacia-korea': '韓國工作室: Espacia Korea',
        'filter.category.option.korean-studios-loozy': '韓國工作室: Loozy',
        'filter.category.option.japanese-studios-graphis': '日本工作室: Graphis',
        'filter.category.option.japanese-studios-kuni-scan': '日本工作室: KUNI Scan',
        'filter.category.option.japanese-studios-weekly-post-digital-photo': '日本工作室: 週刊ポストデジタル寫真集',
        'filter.category.option.japanese-studios-morning-sexy': '日本工作室: アサ芸SEXY',
        'filter.category.option.japanese-studios-prestige': '日本工作室: Prestige',
        'filter.category.option.japanese-studios-x-city': '日本工作室: X-City',
        'filter.category.option.japanese-studios-friday': '日本工作室: FRIDAY',
        'filter.category.option.japanese-studios-super-pose-book': '日本工作室: Super Pose Book',
        'filter.category.option.japanese-studios-urabon': '日本工作室: Urabon',
        'filter.category.option.japanese-studios-escape': '日本工作室: Escape',
        'filter.category.option.japanese-studios-flash': '日本工作室: FLASHデジタル寫真集',
        'filter.category.option.taiwan-studios-jvid': '臺灣工作室: JVID',
        'filter.category.option.taiwan-studios-fantasy-factory': '臺灣工作室: Fantasy Factory',
        'filter.category.option.taiwan-studios-tpimage': '臺灣工作室: TPimage',
        'filter.category.option.others-ai-photos': '其他套圖: AI圖區',
        'config.image_quality.title': '圖片品質',
        'config.image_quality.summary': '選擇圖片品質。原圖(JPG)品質最高，高清(WebP)載入更快。',
    },
}
_YELLOWNOTE_CATEGORIES = (
    ('photos/album-1', 'filter.category.option.theme.xiuren-featured'),
    ('photos/album-2', 'filter.category.option.theme.large-scale'),
    ('photos/album-3', 'filter.category.option.theme.sex'),
    ('photos/album-4', 'filter.category.option.theme.exposure'),
    ('photos/album-5', 'filter.category.option.theme.cosplay'),
    ('photos/album-6', 'filter.category.option.theme.sex-toy'),
    ('photos/album-7', 'filter.category.option.theme.bondage'),
    ('photos/album-8', 'filter.category.option.theme.shaved-pussy'),
    ('photos/album-9', 'filter.category.option.theme.lesbian'),
    ('photos/album-10', 'filter.category.option.theme.with-original-photos'),
    ('photos/album-11', 'filter.category.option.theme.with-video'),
    ('amateurs', 'filter.category.option.theme.amateur'),
    ('photos/series-637b2029d2347', 'filter.category.option.taiwan-studios-jvid'),
    ('photos/series-5f889afb37619', 'filter.category.option.taiwan-studios-fantasy-factory'),
    ('photos/series-5f7a0a80d3d66', 'filter.category.option.taiwan-studios-tpimage'),
    ('photos/series-6310ce9b90056', 'filter.category.option.chinese-studios-pans'),
    ('photos/series-6666a7ac3ba9c', 'filter.category.option.chinese-studios-wind-sings'),
    ('photos/series-64f44d99ce673', 'filter.category.option.chinese-studios-xing-se'),
    ('photos/series-665f8bafab4bc', 'filter.category.option.chinese-studios-huang-fu'),
    ('photos/series-665f7d787d681', 'filter.category.option.chinese-studios-other-studios'),
    ('photos/series-5f1dcdeaee582', 'filter.category.option.chinese-studios-metcn'),
    ('photos/series-5f1d784995865', 'filter.category.option.chinese-studios-litu'),
    ('photos/series-638e5a60b1770', 'filter.category.option.chinese-studios-midnight-project'),
    ('photos/series-5f23c44cd66bd', 'filter.category.option.chinese-studios-pandora'),
    ('photos/series-5f2089564c6c2', 'filter.category.option.chinese-studios-missleg'),
    ('photos/series-646c69b675f3d', 'filter.category.option.chinese-studios-iss'),
    ('photos/series-5f15f389e993e', 'filter.category.option.chinese-studios-aiss'),
    ('photos/series-5f60b98248a81', 'filter.category.option.chinese-studios-au'),
    ('photos/series-622c7f95220a4', 'filter.category.option.chinese-studios-beijing-angel'),
    ('photos/series-619a92aa1fa7a', 'filter.category.option.chinese-studios-wuji-works'),
    ('photos/series-676c3e9b90749', 'filter.category.option.chinese-studios-pomelo'),
    ('photos/series-5f382ba894af4', 'filter.category.option.chinese-studios-sk-silk'),
    ('photos/series-5f15f727df393', 'filter.category.option.chinese-studios-ddy'),
    ('photos/series-5f22ea422221c', 'filter.category.option.chinese-studios-dongguan-vgirls'),
    ('photos/series-61b997728043b', 'filter.category.option.chinese-studios-youmei'),
    ('photos/series-6443d480eb757', 'filter.category.option.others-ai-photos'),
    ('photos/series-665f81885f103', 'filter.category.option.korean-studios-makemodel'),
    ('photos/series-6224e755e21f4', 'filter.category.option.korean-studios-pure-media'),
    ('photos/series-665a2385a2367', 'filter.category.option.korean-studios-espacia-korea'),
    ('photos/series-62888afad416b', 'filter.category.option.korean-studios-loozy'),
    ('photos/series-6450b47c9db0b', 'filter.category.option.japanese-studios-graphis'),
    ('photos/series-66f9665804471', 'filter.category.option.japanese-studios-kuni-scan'),
    ('photos/series-66e68b9c96ab0', 'filter.category.option.japanese-studios-weekly-post-digital-photo'),
    ('photos/series-670d7142b3d88', 'filter.category.option.japanese-studios-morning-sexy'),
    ('photos/series-670791f5f2f0f', 'filter.category.option.japanese-studios-prestige'),
    ('photos/series-66fb8cca706ae', 'filter.category.option.japanese-studios-x-city'),
    ('photos/series-66659e2d94489', 'filter.category.option.japanese-studios-friday'),
    ('photos/series-62a0a15911f16', 'filter.category.option.japanese-studios-super-pose-book'),
    ('photos/series-6692ea004cc75', 'filter.category.option.japanese-studios-urabon'),
    ('photos/series-66603af933ec9', 'filter.category.option.japanese-studios-escape'),
    ('photos/series-672a2029d6a32', 'filter.category.option.japanese-studios-flash'),
    ('photos/series-64be21c972ca4', 'filter.category.option.other-photos-chinese-nude'),
    ('photos/series-64be22b4a0fa0', 'filter.category.option.other-photos-korean-nude'),
    ('photos/series-64be21ef4cc51', 'filter.category.option.other-photos-taiwan-nude'),
    ('photos/series-64be239ce73d4', 'filter.category.option.other-photos-other-regions'),
    ('photos/series-6660093348354', 'filter.category.option.xiuren-all'),
    ('photos/series-66600a3a227ee', 'filter.category.option.xiuren-leaked'),
    ('photos/series-5fc4ce40386af', 'filter.category.option.xiuren-huayang'),
    ('photos/series-5f1495dbda4de', 'filter.category.option.xiuren-mygirl'),
    ('photos/series-5f71afc92d8ab', 'filter.category.option.xiuren-imiss'),
    ('photos/series-5f1dd5a7ebe9a', 'filter.category.option.xiuren-miitao'),
    ('photos/series-5f14a3105d3e8', 'filter.category.option.xiuren-feilin'),
    ('photos/series-60673bec9dd11', 'filter.category.option.xiuren-youwu'),
    ('photos/series-63d435352808c', 'filter.category.option.xiuren-wings'),
    ('photos/series-61263de287e2f', 'filter.category.option.xiuren-ruisg'),
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


class YellowNoteSource(MadaraSource):
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


class GeneratedYellowNoteSource(YellowNoteSource):
    name = 'yellownote_en'
    display_name = '小黄书'
    base_url = 'https://en.xchina.co'
    language = 'en'
    requests_per_minute = 60
    content_warning = 'nsfw'
    image_headers = {'Referer': 'https://en.xchina.co/'}


SOURCE = GeneratedYellowNoteSource
