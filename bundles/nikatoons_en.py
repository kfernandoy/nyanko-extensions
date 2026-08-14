from __future__ import annotations

"""Infraestructura compartida por todos los motores de bundles Nyanko Source v4.

Aqui vive lo que NO es de ningun tema en concreto: el parser de HTML, los helpers
de imagen y fecha, el descifrado de paginas protegidas y la clase base con lo
minimo que toda fuente necesita (`__init__`, `_request`, `page_bytes`).

Existe para que un motor de tema NO tenga que heredar de `MadaraSource`. Antes lo
hacia: 63 de 64 motores heredaban de Madara solo para tener `_request` (5 lineas)
y el parser, y a cambio cada bundle arrastraba `madara.py` entero -- 129 KB, con
el motor Madara y hasta diez fuentes concretas ajenas dentro. Un arreglo del
motor Madara movia el sha256 de 1851 extensiones y la app las marcaba todas como
actualizables, aunque su sitio no tuviera nada que ver con Madara.

El contenido se EXTRAJO de `madara.py` sin tocarlo, para no cambiar comportamiento
al reorganizar.
"""


import base64
import hashlib
import io
import json
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import unquote, urljoin, urlparse, urlunparse

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

# Re-exportados para los motores de tema que se concatenan detras en el bundle:
# los usan en sus firmas y antes los tomaban del namespace de madara.py.
__all__ = [
    "FuenteBaseSource",
    "SourceChapter",
    "SourceFilter",
    "SourcePage",
    "SourcePreference",
    "SourceSeries",
]


def _es_no_encontrado(error: BaseException) -> bool:
    """`True` si la excepcion representa un 404 de la fuente.

    No se hace `except httpx.HTTPStatusError` porque el error puede llegar de dos
    formas segun quien envuelva al fetcher: `httpx.HTTPStatusError` crudo, o el
    `SourceNotFoundError` en que lo traduce la app. Se cubren ambas sin importar
    httpx aqui, que este motor no lo trae.
    """
    if isinstance(error, SourceNotFoundError):
        return True
    respuesta = getattr(error, "response", None)
    return getattr(respuesta, "status_code", None) == 404


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
    return _mismo_host_seguro(urljoin(base_url, value), base_url) if value else ""


def _cuerpo_de_formulario(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Convierte ``data=[(clave, valor), ...]`` en ``dict`` antes de salir a la red.

    httpx 0.28 solo trata como formulario los ``data`` que son Mapping; una lista de pares
    la interpreta como cuerpo iterable, o sea un stream SINCRONO, y sobre el cliente async
    de la app aborta con ``RuntimeError: Attempted to send an sync request with an
    AsyncClient instance``. La fuente no llegaba a hacer ni una peticion: browse, latest y
    search morian de golpe (haremdekira, "no carga nada" en la validacion manual).

    Se normaliza aqui, en el unico embudo por el que salen todas las peticiones del motor,
    en vez de en cada helper. Las claves de estos formularios son unicas -van indexadas
    como ``vars[meta_query][0][key]``-, asi que pasar por ``dict`` no pierde nada; se
    comprobo sobre 762 combinaciones de filtros. Si alguna vez hiciera falta repetir una
    clave, habria que pasarla ya codificada como ``content=``.
    """
    cuerpo = kwargs.get("data")
    if isinstance(cuerpo, (list, tuple)):
        kwargs = dict(kwargs)
        kwargs["data"] = dict(cuerpo)
    return kwargs


def _mismo_host_seguro(url: str, base_url: str) -> str:
    """Sube a https las URLs http:// del propio sitio cuando este ya sirve por https.

    Varios temas Madara emiten las portadas y las paginas del capitulo con el esquema
    en claro aunque el sitio se sirva por https (catharsisworld: 8 de 8 paginas y 16 de
    16 portadas). En Python da igual y por eso el arnes las descargaba sin quejarse,
    pero Android bloquea el trafico cleartext desde API 28, asi que la imagen nunca
    llegaba al lector y el capitulo se veia en blanco.

    Solo se reescribe cuando el host es exactamente el de ``base_url`` y este es https;
    los CDN de terceros se dejan intactos porque no hay garantia de que tengan
    certificado valido.
    """
    if not url.startswith("http://") or not base_url.startswith("https://"):
        return url
    if urlparse(url).netloc.lower() != urlparse(base_url).netloc.lower():
        return url
    return "https://" + url[len("http://"):]


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
            return _mismo_host_seguro(urljoin(base_url, node.attrs[key].strip()), base_url)
    candidates = [
        item.strip().split()[0]
        for item in node.attrs.get("srcset", "").split(",")
        if item.strip()
    ]
    if candidates:
        return _mismo_host_seguro(urljoin(base_url, candidates[-1]), base_url)
    return _style_image_url(node, base_url)


def _es_imagen_de_carga(node: _Node) -> bool:
    """`True` si el <img> es el spinner del tema y no la portada.

    Algunos temas Madara meten un placeholder ANTES de la portada real
    (taurusfansub: `<div class="manga-loader"><img alt="Loading..."></div>` seguido de
    `<div class="manga__thumb_item"><img ...la portada...>`). Coger el primer <img> del
    contenedor devolvia el mismo spinner para las 12 series del listado.

    Se detecta por el alt y por la clase del contenedor, no por la URL: el archivo
    concreto cambia de un sitio a otro.
    """
    if "load" in node.attrs.get("alt", "").casefold():
        return True
    padre = node.parent
    saltos = 0
    while padre is not None and saltos < 3:
        clases = padre.attrs.get("class", "").casefold()
        if "loader" in clases or "loading" in clases:
            return True
        padre = padre.parent
        saltos += 1
    return False


def _cover_url(container: _Node, base_url: str) -> str | None:
    """Portada del contenedor: primero el <img>, si no el background del CSS.

    Es aditivo: el fallback de ``background-image`` solo entra cuando no hay
    ningun ``<img>`` con URL utilizable, asi que no puede cambiar el resultado
    de los sitios que hoy funcionan.
    """
    image = _first(
        container,
        lambda node: node.tag == "img" and not _es_imagen_de_carga(node),
    )
    if image is not None and (url := _image_url(image, base_url)):
        return url
    # Si solo habia loaders, se reintenta sin el filtro antes de pasar al CSS: es
    # preferible un placeholder a quedarse sin portada.
    image = _first(container, lambda node: node.tag == "img")
    if image is not None and (url := _image_url(image, base_url)):
        return url
    if url := _style_image_url(container, base_url):
        return url
    styled = _first(container, lambda node: bool(_style_image_url(node, base_url)))
    return _style_image_url(styled, base_url) if styled is not None else None


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

    result = b""
    previous = iv
    for offset in range(0, len(ciphertext), 16):
        block = ciphertext[offset:offset + 16]
        decrypted = decrypt_block(block)
        result += bytes(left ^ right for left, right in zip(decrypted, previous))
        previous = block
    return result[:-result[-1]] if result else result


def _evp_kdf_decrypt(ciphertext: str, salt: str, password: str, iv: str | None = None) -> str:
    """Descifra el AES-256-CBC que produce `CryptoJS.AES.encrypt` con passphrase.

    CryptoJS no usa la passphrase como clave: la pasa por EvpKDF (MD5 iterado sobre
    password+salt) hasta sacar 48 bytes, de los que los 32 primeros son la clave y los
    16 siguientes el IV. Cuando el payload trae `iv` propio se usa ese en su lugar.

    Se reimplementa AES en Python puro, igual que `generic.py`, porque los bundles se
    generan autocontenidos y no pueden arrastrar dependencias externas.
    """
    generado = b""
    digest = b""
    password_bytes = password.encode()
    salt_bytes = bytes.fromhex(salt)
    while len(generado) < 48:
        digest = hashlib.md5(digest + password_bytes + salt_bytes).digest()
        generado += digest
    vector = bytes.fromhex(iv) if iv else generado[32:48]
    return _aes256_decrypt(base64.b64decode(ciphertext), generado[:32], vector).decode()


def _protected_page_urls(html: str, base_url: str) -> list[str]:
    """Paginas del plugin `wp-manga-chapter-images-protection`.

    Ese plugin sustituye los <img> del lector por divs `.page-break` vacios y publica la
    lista real cifrada en `var chapter_data`. Sin esto la extension devuelve 0 paginas
    aunque el capitulo exista (catharsisworld: 17 imagenes por capitulo).

    La passphrase es uno de los nonces de 10 hex que WordPress ya imprime en la pagina;
    no hay forma fiable de saber cual, asi que se prueban en orden y se acepta el primero
    que produzca un JSON con lista de URLs.
    """
    payload = re.search(r"var\s+chapter_data\s*=\s*'([^']+)'", html)
    if payload is None:
        return []
    try:
        datos = json.loads(payload.group(1).replace("\\/", "/"))
    except json.JSONDecodeError:
        return []
    if not datos.get("ct") or not datos.get("s"):
        return []

    candidatas: list[str] = []
    for encontrado in re.finditer(r"""["']([0-9a-f]{10})["']""", html):
        if encontrado.group(1) not in candidatas:
            candidatas.append(encontrado.group(1))

    for clave in candidatas[:12]:
        try:
            valor = json.loads(
                _evp_kdf_decrypt(datos["ct"], datos["s"], clave, datos.get("iv"))
            )
            while isinstance(valor, str):
                valor = json.loads(valor)
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(valor, list) and valor:
            return [
                urljoin(base_url, str(item).strip().replace("\\/", "/"))
                for item in valor
                if str(item).strip()
            ]
    return []


class FuenteBaseSource:
    """Base minima de toda fuente: identidad, capacidades y peticion HTTP.

    Deliberadamente NO trae `browse`, `search`, `details`, `chapters` ni `pages`:
    eso es trabajo de cada tema. Quien herede de aqui debe implementarlos.
    """

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

    _ESTADOS_BADGE = {
        "ongoing", "oncoming", "on going", "completed", "completo", "completado",
        "finalizado", "concluido", "en curso", "curso", "pausado", "en espera",
        "on hold", "canceled", "cancelado", "hiatus", "publicandose", "en emision",
    }

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
        kwargs = _cuerpo_de_formulario(kwargs)
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
    from .base import (
        FuenteBaseSource,
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


class MangaThemesiaSource(FuenteBaseSource):
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
    name = 'nikatoons_en'
    display_name = 'Nika Toons'
    base_url = 'https://nikatoons.com'
    language = 'en'
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
