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

"""Implementación HTML común de GalleryAdults."""

import json
import re
from urllib.parse import urljoin, urlparse

try:
    from .base import FuenteBaseSource, SourceChapter, SourcePage, SourceSeries, _first, _image_url, _parse_html
except ImportError:
    pass


class GalleryAdultsSource(FuenteBaseSource):
    manga_language = ""
    profile = ""

    @staticmethod
    def _es_enlace_de_galeria(href: str) -> bool:
        """`True` si el href apunta a la ficha de una obra.

        Los sitios del tema no comparten prefijo: imhentai, hentaiera, hentaizap y
        hentaienvy usan `/gallery/123/`, mientras que asmhentai y nhentai.xxx usan
        `/g/123/`. Filtrar solo por `/gallery/` dejaba a estos dos cayendo al enlace
        de categoria, asi que el listado colapsaba a 2 tarjetas repetidas.
        """
        ruta = urlparse(href).path
        return bool(re.search(r"/(?:gallery|g|view)/\d+", ruta))

    @staticmethod
    def _es_bandera(node) -> bool:
        """`True` si el <img> es la banderita de idioma de la tarjeta, no la portada.

        Cada tarjeta abre con `<div class="cat_flag"><img class="thumb_flag"
        src="/images/esp.png">` ANTES del `<div class="inner_thumb">` que lleva la
        portada real. Coger el primer <img> del contenedor devolvia esa bandera, asi
        que el listado entero salia con `esp.png` / `uk_usa.png` de portada.

        Se descarta por clase y por ruta: `thumb_flag` es la clase del tema y
        `/images/` es la carpeta de assets estaticos del sitio, mientras que las
        portadas viven siempre en el CDN (`m10.imhentai.xxx/...`).
        """
        clases = node.attrs.get("class", "").split()
        if "thumb_flag" in clases:
            return True
        padre = node.parent
        saltos = 0
        while padre is not None and saltos < 2:
            if "cat_flag" in padre.attrs.get("class", "").split():
                return True
            padre = padre.parent
            saltos += 1
        for clave in ("data-src", "data-lazy-src", "src"):
            valor = node.attrs.get(clave, "").strip()
            # El `src` inicial es un SVG en data: URI (el placeholder del lazy-load);
            # no delata nada, asi que solo se mira la ruta de assets del sitio.
            if valor.startswith("data:"):
                continue
            if valor and "/images/" in urlparse(valor).path:
                return True
        return False

    def _portada(self, item, base: str) -> str | None:
        image = _first(
            item,
            lambda node: node.tag == "img" and not self._es_bandera(node),
        )
        # Si en la tarjeta SOLO habia banderas se cae al comportamiento anterior: es
        # preferible una portada equivocada a quedarse sin ninguna.
        image = image or _first(item, lambda node: node.tag == "img")
        return _image_url(image, base) if image else None

    def _series(self, html: str, base: str) -> list[SourceSeries]:
        root = _parse_html(html)
        classes = {"thumb", "preview_item", "gallery_item"}
        result: list[SourceSeries] = []
        for item in (
            node
            for node in root.descendants()
            if classes.intersection(node.attrs.get("class", "").split())
        ):
            # El PRIMER <a> de la tarjeta es el de la categoria (`/category/doujinshi/`),
            # no el de la galeria: quedarse con el hacia que las 25 tarjetas de la pagina
            # compartieran titulo ("Doujinshi", "Western") y el dedupe final las colapsara
            # a 4 o 5. Se prefiere el enlace que apunta a una galeria.
            enlaces = [
                node for node in item.descendants("a") if node.attrs.get("href")
            ]
            link = next(
                (node for node in enlaces if self._es_enlace_de_galeria(node.attrs["href"])),
                # Sin enlace de galeria reconocible se descarta la tarjeta: caer al
                # primer <a> devolvia la categoria o el idioma, y el dedupe final
                # colapsaba la pagina entera a 2-4 entradas falsas.
                None,
            )
            caption = _first(
                item,
                lambda node: any(
                    name in node.attrs.get("class", "").split()
                    # `gallery_title` es el titulo real de la obra; `gallery_cat` es la
                    # categoria y se descarta a proposito.
                    for name in ("gallery_title", "caption", "title", "tag_name")
                )
                and node.text(),
            )
            title = caption.text() if caption else link.text() if link else ""
            if link and title:
                source_id = urljoin(base, link.attrs["href"])
                result.append(
                    SourceSeries(
                        source_id=source_id,
                        title=title,
                        source_name=self.name,
                        cover_url=self._portada(item, base),
                        web_url=source_id,
                    )
                )
        return list({item.source_id: item for item in result}.values())

    async def _catalog(self, popular: bool, page: int) -> list[SourceSeries]:
        path = self.base_url
        if self.manga_language:
            path += f"/language/{self.manga_language}"
        if popular:
            path += "/popular"
        # La barra final NO es opcional: `/language/spanish/popular` devuelve 404 y solo
        # `/language/spanish/popular/` responde el listado. El engine la omitia, asi que
        # el catalogo entero salia vacio en las 8 variantes.
        if not path.endswith("/"):
            path += "/"
        response = await self._request("GET", path, params={"page": max(page, 1)})
        response.raise_for_status()
        return self._series(response.text, path)

    async def search(self, query: str, limit: int = 20) -> list[SourceSeries]:
        response = await self._request(
            "GET",
            f"{self.base_url}/search/",
            params={"q": query.strip(), "key": query.strip(), "page": 1},
        )
        response.raise_for_status()
        return self._series(response.text, self.base_url)[:limit]

    async def browse(self, kind: str, page: int = 1) -> list[SourceSeries]:
        if kind not in {"popular", "latest"}:
            return []
        return await self._catalog(kind == "popular", page)

    async def details(self, series: SourceSeries | str) -> SourceSeries:
        """Ficha de la galeria.

        El `details` heredado de Madara busca `post-title`, `summary_image` y
        `post-content_item`, que este tema no emite: la ficha salia entera vacia
        (sin portada, sin autor, sin tags).

        Los seis sitios del tema tienen markups distintos para la lista de etiquetas
        (`span.tags_text` + `a.tag` en imhentai, `div.tags` + `span.badge` en
        asmhentai, `span.info_txt` + `a.gp_btn_tag` en hentaizap, `li.tags` +
        `span.tag_name` en nhentai.xxx), asi que NO se clasifica por clase CSS sino
        por el prefijo del href, que si es uniforme: `/tag/`, `/artist/`, `/parody/`,
        `/category/`. El contador de cada etiqueta va en un `<span>` hijo y se
        descuenta del texto para no acabar con "nakadashi 225889".
        """
        series_id = series.source_id if isinstance(series, SourceSeries) else str(series)
        response = await self._request("GET", urljoin(f"{self.base_url}/", series_id))
        response.raise_for_status()
        root = _parse_html(response.text)
        base = str(response.url)

        titulo = _first(root, lambda node: node.tag == "h1")
        subtitulo = _first(root, lambda node: node.tag == "p" and node.has_class("subtitle"))
        # La portada de la ficha es `.../cover.jpg`; el resto de <img> son las
        # miniaturas `1t.jpg`, `2t.jpg`... del previsualizador.
        portada = _first(
            root,
            lambda node: node.tag == "img"
            and not self._es_bandera(node)
            and "cover" in _image_url(node, base).rsplit("/", 1)[-1],
        )
        portada = portada or _first(
            root,
            lambda node: node.tag == "img"
            and node.has_class("lazy")
            and not self._es_bandera(node),
        )

        def campo(*prefijos: str) -> list[str]:
            valores: list[str] = []
            for enlace in root.descendants("a"):
                ruta = urlparse(enlace.attrs.get("href", "")).path
                if not any(ruta.startswith(f"/{prefijo}/") for prefijo in prefijos):
                    continue
                texto = enlace.text()
                for hijo in enlace.descendants("span"):
                    # Solo se descuentan los <span> HOJA: en asmhentai el contador va
                    # dentro de `<span class="badge tag">`, que envuelve tambien al
                    # nombre, y recortar el envoltorio dejaba la etiqueta vacia.
                    if any(True for _ in hijo.descendants("span")):
                        continue
                    clases = hijo.attrs.get("class", "").split()
                    if any(
                        marca in clase
                        for clase in clases
                        for marca in ("badge", "count")
                    ):
                        texto = texto.replace(hijo.text(), " ")
                if texto := " ".join(texto.split()):
                    valores.append(texto)
            return valores

        artistas = campo("artist")
        etiquetas = [*campo("tag"), *campo("category"), *campo("parody")]
        return SourceSeries(
            source_id=series_id,
            title=titulo.text().strip() if titulo else (
                series.title if isinstance(series, SourceSeries)
                else series_id.rstrip("/").rsplit("/", 1)[-1]
            ),
            source_name=self.name,
            cover_url=_image_url(portada, base) if portada else (
                series.cover_url if isinstance(series, SourceSeries) else None
            ),
            description=subtitulo.text().strip() if subtitulo else None,
            author=", ".join(artistas) or None,
            artist=", ".join(artistas) or None,
            content_tags=tuple(dict.fromkeys(etiquetas)),
            web_url=base,
            metadata=series.metadata if isinstance(series, SourceSeries) else {},
        )

    async def chapters(self, series: SourceSeries | str) -> list[SourceChapter]:
        series_id = series.source_id if isinstance(series, SourceSeries) else series
        return [
            SourceChapter(
                source_id=series_id,
                title="Chapter",
                series_id=series_id,
                source_name=self.name,
            )
        ]

    @staticmethod
    def _inputs(root) -> dict[str, str]:
        return {
            node.attrs["id"]: node.attrs.get("value", "")
            for node in root.descendants("input")
            if node.attrs.get("id")
        }

    async def pages(self, chapter: SourceChapter | str) -> list[SourcePage]:
        chapter_id = chapter.source_id if isinstance(chapter, SourceChapter) else chapter
        response = await self._request("GET", chapter_id)
        response.raise_for_status()
        root = _parse_html(response.text)
        inputs = self._inputs(root)
        scripts = "\n".join(node.text() for node in root.descendants("script"))
        encoded = re.search(r"\$\.parseJSON\('(.+?)'\)", scripts, re.DOTALL)
        urls: list[str] = []
        if encoded:
            payload = json.loads(encoded.group(1).encode().decode("unicode_escape"))
            # nhentai.xxx no emite `{"1": "j,1280,1850", ...}` como los demas, sino
            # `{"fl": {...paginas...}, "th": {...miniaturas...}, "ct": {...portada...}}`.
            # Iterando el nivel 1 salian 3 "paginas" llamadas fl/th/ct. Se baja al
            # sub-diccionario de las imagenes completas cuando el payload viene anidado.
            if payload and all(isinstance(valor, dict) for valor in payload.values()):
                payload = payload.get("fl") or max(payload.values(), key=len)
            load_dir = inputs.get("load_dir", "").strip("/")
            load_id = inputs.get("load_id", "")
            server_number = inputs.get("load_server", "")
            # El host del CDN se deduce de la portada. NO se busca por clase: en
            # hentaifox el <img> de la ficha no lleva ninguna (`<img src=".../cover.jpg">`),
            # asi que el filtro `cover|img-responsive` no encontraba nada, se caia al
            # host del sitio y las 137 paginas apuntaban a `hentaifox.com/...` -> 404.
            # Se busca por el nombre del archivo, que si es constante en los 8 sitios.
            cover = _first(
                root,
                lambda node: node.tag == "img"
                and urlparse(_image_url(node, chapter_id)).path.rsplit("/", 1)[-1].startswith("cover."),
            )
            host_portada = urlparse(_image_url(cover, chapter_id)).hostname if cover else None
            # La portada MANDA sobre `load_server`. Ese input solo dice el numero de
            # servidor, y componerlo como `m{n}.{dominio del sitio}` presupone que el
            # CDN vive bajo el mismo dominio: cierto en imhentai/hentaiera, falso en
            # nhentai.xxx, cuyo CDN es `i2.nhentaimg.com`. Ahi se generaban 199 URLs
            # apuntando a `m2.nhentai.xxx`, un host que ni siquiera resuelve.
            server = host_portada or (
                f"m{server_number}.{urlparse(self.base_url).hostname}"
                if server_number
                else urlparse(self.base_url).hostname
            )
            for key, value in payload.items():
                code = str(value).split(",", 1)[0].strip('"')
                extension = {"p": "png", "b": "bmp", "g": "gif", "w": "webp"}.get(code, "jpg")
                urls.append(f"https://{server}/{load_dir}/{load_id}/{key}.{extension}")
        else:
            # Sin payload JSON (asmhentai) las paginas se derivan de las miniaturas
            # `1t.jpg` -> `1.jpg`. El <img> NO es hijo directo del `.preview_thumb`:
            # va dentro de un `<a>`, asi que mirar solo `node.parent` no encontraba
            # ninguna y la galeria salia con 0 paginas. Se sube por los ancestros.
            for node in root.descendants("img"):
                ancestro = node.parent
                saltos = 0
                contenedor = False
                while ancestro is not None and saltos < 3:
                    if any(
                        name in ancestro.attrs.get("class", "").split()
                        for name in ("gallery_thumb", "preview_thumb")
                    ):
                        contenedor = True
                        break
                    ancestro = ancestro.parent
                    saltos += 1
                if not contenedor:
                    continue
                url = _image_url(node, chapter_id)
                extension = url.rsplit(".", 1)[-1]
                urls.append(url.replace(f"t.{extension}", f".{extension}"))
            # El previsualizador solo pinta las 10 primeras y deja el resto tras un
            # boton "View More". El total real esta en `<input id="t_pages">`, y las
            # URLs son correlativas, asi que se completan sin pedir el ajax: sin esto
            # una galeria de 203 paginas se leia como si tuviera 10.
            total = inputs.get("t_pages", "").strip()
            if urls and total.isdigit() and len(urls) < int(total):
                base_url, _, ultimo = urls[0].rpartition("/")
                numero, punto, extension = ultimo.rpartition(".")
                if numero.isdigit() and punto:
                    urls = [
                        f"{base_url}/{indice}{punto}{extension}"
                        for indice in range(1, int(total) + 1)
                    ]
        return [
            SourcePage(
                source_id=url,
                chapter_id=chapter_id,
                index=index,
                filename=url.rsplit("/", 1)[-1].split("?", 1)[0],
                source_name=self.name,
            )
            for index, url in enumerate(urls, 1)
        ]

class GeneratedGalleryAdultsSource(GalleryAdultsSource):
    name = 'hentaifox_ja'
    display_name = 'HentaiFox (ja)'
    base_url = 'https://hentaifox.com'
    language = 'ja'
    manga_language = 'japanese'
    profile = 'hentaifox'


SOURCE = GeneratedGalleryAdultsSource

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
