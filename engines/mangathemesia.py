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
