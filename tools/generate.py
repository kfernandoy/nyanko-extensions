"""Genera bundles Madara soportados, sus iconos y el índice instalable."""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import re
import shutil
import tokenize
from pathlib import Path


def _extract_kotlin_metadata(module: Path) -> str:
    files = [*module.rglob("*.kt"), *module.rglob("*.gradle.kts")]
    contents = [source_file.read_text(encoding="utf-8", errors="ignore") for source_file in files]
    for marker, value in (
        ("ContentWarning.NSFW", "nsfw"),
        ("ContentWarning.MIXED", "mixed"),
        ("ContentWarning.SAFE", "safe"),
    ):
        if any(marker in content for content in contents):
            return value
    return "nsfw" if any("adult" in content.lower() for content in contents) else "unknown"


def _refrescar_motor_en_manual(source: str, engine: str) -> str:
    """Cambia la copia congelada del motor que lleva el manual por el motor actual.

    Cada archivo de ``engines/manual`` es [copia del motor] + [logica propia]. Esa copia se
    hizo una vez y no se vuelve a tocar, asi que los arreglos del motor NUNCA le llegaban:
    851 manuales seguian con la version vieja de ``chapters()``, 912 sin el descifrado del
    plugin de imagenes y 884 sin el filtro del spinner. Como ``generate.py`` usa el manual
    tal cual en lugar de inlinear ``madara.py``, esos bundles se publicaban con codigo de
    varios commits atras.

    Se sustituye solo el prefijo comun. El corte se busca en dos sitios, por ese orden:

      1. ``try: from .madara import ...`` -- lo traen 700 manuales.
      2. la primera subclase de ``MadaraSource`` -- otros 151 empiezan asi su parte propia.

    En el caso 2 no basta con cortar en la subclase: varios manuales definen ANTES helpers
    propios a nivel de modulo (``_nartag_last_child``, ``_raven_kids``...) que la subclase
    usa. Cortar en la clase los dejaba fuera y el bundle petaba con NameError en cuanto se
    llamaba a browse. Por eso se retrocede hasta la primera definicion top-level que ya no
    pertenece al motor.

    Lo que va despues -que es lo unico que justifica el override- se conserva intacto. Si no
    aparece ninguno de los dos se devuelve el archivo sin tocar; son los 61 de MangaDex, que
    no derivan de este motor y no tienen nada que refrescar.
    """
    # Los 61 manuales de MangaDex no contienen MadaraSource y quedan fuera.
    try:
        manual_tree = ast.parse(source)
        engine_tree = ast.parse(engine)
    except SyntaxError:
        # Algunas copias antiguas contienen regex de comillas simples que _manual_bundle
        # sanea despues. Se usa el corte textual probado para ellas, pero se preserva toda
        # declaracion propia anterior a la subclase.
        return _refrescar_motor_en_manual_textual(source, engine)

    madara = next(
        (
            node
            for node in manual_tree.body
            if isinstance(node, ast.ClassDef) and node.name == "MadaraSource"
        ),
        None,
    )
    if madara is None:
        return source

    lineas = source.splitlines(keepends=True)
    nombres_motor = _nombres_top_level(engine_tree)
    propios: list[str] = []
    for node in manual_tree.body:
        if node.lineno <= madara.end_lineno:
            continue
        if _es_import_madara_vacio(node):
            continue
        nombres = _nombres_de_nodo(node)
        colisiones = nombres & nombres_motor
        if colisiones:
            raise ValueError(
                "el manual redefine nombres del motor actual: "
                + ", ".join(sorted(colisiones))
            )
        propios.append("".join(lineas[node.lineno - 1 : node.end_lineno]))

    return engine.rstrip() + "\n\n\n" + "\n\n".join(propios).lstrip()


def _nombres_de_nodo(node: ast.AST) -> set[str]:
    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        return {node.name}
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        return {target.id for target in targets if isinstance(target, ast.Name)}
    return set()


def _nombres_top_level(tree: ast.Module) -> set[str]:
    return {nombre for node in tree.body for nombre in _nombres_de_nodo(node)}


def _es_import_madara_vacio(node: ast.AST) -> bool:
    if not isinstance(node, ast.Try) or node.finalbody or node.orelse:
        return False
    if not node.body or not all(isinstance(child, ast.ImportFrom) for child in node.body):
        return False
    if not all(child.module == "madara" and child.level == 1 for child in node.body):
        return False
    return (
        len(node.handlers) == 1
        and isinstance(node.handlers[0].type, ast.Name)
        and node.handlers[0].type.id == "ImportError"
        and len(node.handlers[0].body) == 1
        and isinstance(node.handlers[0].body[0], ast.Pass)
    )


def _refrescar_motor_en_manual_textual(source: str, engine: str) -> str:
    """Fallback para los manuales historicos que aun no parsean antes del saneado."""
    marca = re.search(r"^try:\s*\n\s*from \.madara import", source, re.M)
    if marca is not None:
        inicio_propio = marca.start()
    else:
        subclase = re.search(r"^class \w+\(MadaraSource\):", source, re.M)
        if subclase is None:
            return source
        inicio_propio = subclase.start()

    nombres_motor = {
        nombre
        for definicion in re.finditer(
            r"^(?:(?:def|class)\s+(\w+)|([A-Za-z_]\w*)\s*(?::[^=\n]+)?=)",
            engine,
            re.M,
        )
        for nombre in definicion.groups()
        if nombre is not None
    }
    imports_motor = {
        linea.strip()
        for linea in engine.splitlines()
        if linea.startswith(("import ", "from "))
    }
    # Retroceder al primer helper, constante o import propio anterior al marcador. Esto
    # conserva `math`, `time`, `datetime`, `unescape`, tablas `_MANTA_*`, etc.
    candidatos: list[int] = []
    for match in re.finditer(
        r"^(?:(?:def|class)\s+(\w+)|([A-Za-z_]\w*)\s*(?::[^=\n]+)?=|(import\s+[^\n]+|from\s+[^\n]+\s+import\s+[^\n]+))",
        source,
        re.M,
    ):
        if match.start() >= inicio_propio:
            break
        if match.start() <= source.find("class MadaraSource:"):
            continue
        nombre = match.group(1) or match.group(2)
        importacion = match.group(3)
        if (nombre and nombre not in nombres_motor) or (
            importacion and importacion.strip() not in imports_motor
        ):
            candidatos.append(match.start())
    if candidatos:
        inicio_propio = min(candidatos)
    return engine.rstrip() + "\n\n\n" + source[inicio_propio:]


def _manual_bundle(path: Path, engine: str = "") -> bytes:
    source = path.read_text(encoding="utf-8")
    if engine:
        source = _refrescar_motor_en_manual(source, engine)
    lines = source.splitlines(keepends=True)
    for index in range(len(lines) - 1):
        if lines[index].strip() == "try:" and lines[index + 1].lstrip().startswith("from .madara import"):
            end = index + 2
            while end < len(lines) and lines[end].strip() != "except ImportError:":
                end += 1
            if end + 1 < len(lines) and lines[end + 1].strip() == "pass":
                del lines[index : end + 2]
            break
    source = "".join(lines)
    source = source.replace(
        "r'<meta[^>]+name=[\"']csrf-token[\"'][^>]+content=[\"']([^\"']+)[\"']'",
        "r\"\"\"<meta[^>]+name=[\"']csrf-token[\"'][^>]+content=[\"']([^\"']+)[\"']\"\"\"",
    )
    source = source.replace(
        "r'<[^>]+class=[\"'][^\"']*page-item[^\"']*[\"'][^>]*>.*?<a[^>]+rel=[\"'][^\"']*next[^\"']*[\"'][^>]+href=[\"']([^\"']+)[\"']'",
        "r\"\"\"<[^>]+class=[\"'][^\"']*page-item[^\"']*[\"'][^>]*>.*?<a[^>]+rel=[\"'][^\"']*next[^\"']*[\"'][^>]+href=[\"']([^\"']+)[\"']\"\"\"",
    )
    replacements = {"true": "True", "false": "False", "null": "None"}
    source = tokenize.untokenize(
        (token.type, replacements.get(token.string, token.string))
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
    )
    return source.encode()


def _source_icon(build_path: Path, source_root: Path, engine_name: str) -> Path:
    candidates = (
        build_path.parent / "res" / "mipmap-xxxhdpi" / "ic_launcher.png",
        source_root.parent
        / "lib-multisrc"
        / engine_name
        / "res"
        / "mipmap-xxxhdpi"
        / "ic_launcher.png",
        Path(__file__).with_name("default_icon.png"),
    )
    return next(path for path in candidates if path.exists())


SUPPORTED_OVERRIDES = {
    "adultContentFilterOptions",
    "altName",
    "altNameSelector",
    "client",
    "chapterUrlSuffix",
    "dateFormat",
    "fetchGenres",
    "filterNonMangaItems",
    "genreConditionFilterOptions",
    "mangaDetailsSelectorArtist",
    "mangaDetailsSelectorAuthor",
    "mangaDetailsSelectorDescription",
    "mangaDetailsSelectorGenre",
    "mangaDetailsSelectorStatus",
    "mangaDetailsSelectorTag",
    "mangaDetailsSelectorThumbnail",
    "mangaDetailsSelectorTitle",
    "mangaSubString",
    "orderByFilterOptions",
    "sendViewCount",
    "seriesTypeSelector",
    "statusFilterOptions",
    "supportsLatest",
    "updatingRegex",
    "useLoadMoreRequest",
    "useNewChapterEndpoint",
    "pageListParseSelector",
}
SUPPORTED_MANGATHEMESIA_OVERRIDES = {
    "altNamePrefix",
    "client",
    "dateFormat",
    "hasProjectPage",
    "mangaUrlDirectory",
    "pageSelector",
    "projectPageString",
    "sendViewCount",
    "seriesAltNameSelector",
    "seriesArtistSelector",
    "seriesAuthorSelector",
    "seriesDescriptionSelector",
    "seriesDetailsSelector",
    "seriesGenreSelector",
    "seriesStatusSelector",
    "seriesThumbnailSelector",
    "seriesTitleSelector",
    "seriesTypeSelector",
    "slugRegex",
    "supportsLatest",
}
IGNORED_MADARA_FUNCTIONS = {
    "chapterDateSelector",
    "fetchSearchManga",
    "genresRequest",
    "getFilterList",
    "getMangaUrl",
    "mangaDetailsParse",
    "mangaDetailsRequest",
    "parseChapterDate",
    "parseGenres",
    "relatedMangaListParse",
    "relatedMangaSelector",
    "setupPreferenceScreen",
}
BROAD_MADARA_FUNCTIONS = {
    "chapterFromElement",
    "chapterListParse",
    "chapterListSelector",
    "fetchPopularManga",
    "imageFromElement",
    "latestUpdatesFromElement",
    "latestUpdatesNextPageSelector",
    "latestUpdatesParse",
    "latestUpdatesSelector",
    "popularMangaFromElement",
    "popularMangaNextPageSelector",
    "popularMangaParse",
    "popularMangaSelector",
    "searchMangaFromElement",
    "searchMangaNextPageSelector",
    "searchMangaParse",
    "searchMangaSelector",
}
IGNORED_MANGATHEMESIA_FUNCTIONS = {
    "fetchMangaDetails",
    "fetchSearchManga",
    "getFilterList",
    "imageRequest",
    "getGenreList",
    "getMangaUrl",
    "mangaDetailsParse",
    "parseGenres",
    "setupPreferenceScreen",
}


def _match(pattern: str, text: str, default: str = "") -> str:
    found = re.search(pattern, text)
    return found.group(1) if found else default


def _source_block(text: str) -> str:
    found = re.search(r"\bsource\s*\{", text)
    if found is None:
        return ""
    depth = 1
    index = found.end()
    while index < len(text) and depth:
        depth += (text[index] == "{") - (text[index] == "}")
        index += 1
    return text[found.end() : index - 1]


def _source_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    offset = 0
    while found := re.search(r"\bsource\s*\{", text[offset:]):
        start = offset + found.end()
        depth = 1
        index = start
        while index < len(text) and depth:
            depth += (text[index] == "{") - (text[index] == "}")
            index += 1
        blocks.append(text[start : index - 1])
        offset = index
    return blocks


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _expand_source(source: str, language: str) -> str:
    source = re.sub(r"\blang\s*=\s*it\b", f'lang = "{language}"', source)
    return re.sub(r"\$(?:it\b|\{it\})", language, source)


def _disambiguate_names(
    extensions: list[dict[str, object]],
    languages: dict[str, str],
) -> None:
    counts: dict[str, int] = {}
    for extension in extensions:
        name = str(extension["name"])
        counts[name] = counts.get(name, 0) + 1
    for extension in extensions:
        name = str(extension["name"])
        language = languages.get(str(extension["id"]), "")
        if counts[name] > 1 and language:
            extension["name"] = f"{name} ({language})"


def _supported_madara(
    module: Path,
    build: str,
    source_override: str = "",
) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"madara"', build):
        return None
    kotlin = "\n".join(path.read_text(encoding="utf-8") for path in module.rglob("*.kt"))
    functions = set(re.findall(r"override\s+fun\s+(\w+)\s*\(", kotlin))
    custom_profiles = {
        "amuy", "bakamh", "barmanga", "begatranslation", "catharsisworld",
        "cerisescans", "domalfansub", "doodmanga", "doujinshell", "emperorscan",
        "fenixproject", "firescans", "ghosthentai", "gourmetscans", "hentai4free",
        "hentaicube", "hentairead", "huntersscans", "inkapk", "isekaiscantop",
        "kissmangain", "kunmangaonline", "kuroimanga", "laviniafansub",
        "lectormangalat", "leitordemangas", "littletyrant", "lunascans",
        "madaradex", "manga18fx", "mangablaze", "mangacrab", "mangadass",
        "mangadistrict", "mangaforfreecom", "mangagezgini", "mangagg",
        "mangahubfr", "mangahe", "mangaisekaithai", "mangalek", "mangalionz",
        "mangasbrasuka", "mangasnosekai", "mangatilkisi", "manhuabug",
        "manhuakey", "manhuarm", "manhuathai", "manhwa18cc", "manhwabreakup",
        "mgkomik", "mhscans", "milasub", "montetai", "mugiwarasoficial",
        "niverafansub", "noindexscan", "novelcrow", "opiatoon", "otascans",
        "paritehaber", "pornhwa18", "ragnarokscanlation", "rdscans", "rocksmanga",
        "strayfansub", "templescanesp", "tiamanhwa", "toonily", "topmanhua",
        "topmanhuafan", "truyentuoitho", "xxxyaoi", "yonabar", "yubikiri",
        "zazamanga",
    }
    if functions - IGNORED_MADARA_FUNCTIONS - BROAD_MADARA_FUNCTIONS and module.name not in custom_profiles:
        return None
    overrides = set(
        re.findall(
            r"override\s+(?:(?:protected|public|private)\s+)?(?:lateinit\s+)?val\s+(\w+)",
            kotlin,
        )
    )
    broad_overrides = {
        "capacity",
        "chapterUrlSelector",
        "mangaEntrySelector",
        "popularMangaUrlSelector",
        "popularMangaUrlSelectorImg",
        "searchMangaUrlSelector",
    }
    if overrides - SUPPORTED_OVERRIDES - broad_overrides and module.name not in custom_profiles:
        return None
    page_selector = _match(
        r'pageListParseSelector[^=]*=\s*"([^"]+)"',
        kotlin,
    )
    source = source_override or _source_block(build)
    base_url = (
        _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source)
        or _match(r'custom\("(https?://[^"]+)"\)', source)
        or _match(r'mirrors\(\s*"(https?://[^"]+)"', source)
    )
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', source) or _match(
        r'\bname\s*=\s*"([^"]+)"', build
    )
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None

    strategy = _match(r"useLoadMoreRequest\s*=\s*LoadMoreStrategy\.(\w+)", kotlin, "AutoDetect")
    # El Kotlin declara la estrategia que le sirve a Mihon, no siempre la que nos sirve a
    # nosotros. barmanga declara Never y usa el selector propio `#loop-content .mp-card`,
    # pero ese markup ya no viaja en el HTML estatico (67% del documento son <style> y solo
    # hay 2 enlaces a /manga/). El catalogo real lo sirve `madara_load_more`, que sI devuelve
    # 16 series con portada y casa con el selector page-item-detail que ya usamos.
    # 'auto' no vale: la deteccion de nav.navigation-ajax ocurre DESPUES del GET, asi que la
    # primera llamada a browse devolveria 0.
    load_more_overrides = {
        "barmanga": "always",
    }
    capacity = int(_match(r"\.rateLimit\(\s*(\d+)", kotlin, "1"))
    seconds = int(_match(r"\.rateLimit\(\s*\d+\s*,\s*(\d+)\.seconds", kotlin, "1"))
    rpm = capacity * 60 // seconds
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
        "manga_substring": _match(
            r'mangaSubString\s*(?::\s*String)?\s*=\s*"([^"]+)"',
            kotlin,
            "manga",
        ),
        "load_more": load_more_overrides.get(
            module.name,
            {"Always": "always", "Never": "never"}.get(strategy, "auto"),
        ),
        "new_chapters": bool(
            re.search(r"useNewChapterEndpoint[^=]*=\s*true", kotlin)
        ),
        "chapter_url_suffix": _match(
            r'chapterUrlSuffix[^=]*=\s*"([^"]*)"',
            kotlin,
            "?style=list",
        ),
        "supports_latest": not bool(re.search(r"supportsLatest\s*=\s*false", kotlin)),
        "rpm": rpm,
        "pages_profile": {
            "cerisescans": "cerise",
            "domalfansub": "login_guard",
            "ghosthentai": "login_guard",
            "hentairead": "hentairead",
            "isekaiscantop": "arraydata",
            "kuroimanga": "login_guard",
            "laviniafansub": "login_guard",
            # `pageListParse` hace POST a un form#redirect-form antes de parsear (ver
            # TempleScanEsp.kt): el HTML del capitulo solo trae el logo del sitio.
            "templescanesp": "redirect_form",
            "lectormangalat": "page_break_only",
            "leitordemangas": "preloaded",
            "littletyrant": "base64_pages",
            "lunascans": "login_guard",
            "mangaforfreecom": "https",
            "mangagezgini": "captcha_guard",
            "mangahe": "skip_placeholder",
            "mangaisekaithai": "scrambled",
            "mangasbrasuka": "campaign",
            "manhuabug": "scrambled",
            "manhuakey": "scrambled",
            "manhuathai": "scrambled",
            "manhwabreakup": "scrambled",
            "milasub": "login_guard",
            "mugiwarasoficial": "campaign",
            "niverafansub": "login_guard",
            "opiatoon": "login_guard",
            "strayfansub": "login_guard",
        }.get(module.name, "default"),
        "extra_headers": (
            {"Accept-Language": "zh-CN,zh;q=0.9"}
            if module.name == "bakamh"
            else {"X-Requested-With": "XMLHttpRequest"}
            if module.name == "mgkomik"
            else {}
        ),
        "image_headers": (
            {
                "Accept": "image/webp,image/*,*/*",
                "Referer": f"{base_url.rstrip('/')}/",
                "X-Reader-Sec": "tiraninha-web",
            }
            if module.name == "littletyrant"
            else {}
        ),
        "date_format": _match(r'dateFormat\s*=\s*SimpleDateFormat\("([^"]+)"', kotlin, "MMMM dd, yyyy"),
        "date_locale": _match(r'SimpleDateFormat\([^\n]+Locale\("([^"]+)"\)', kotlin, "en"),
        "details_profile": "hades" if module.name == "hadesnofansub" else "default",
        "adapter_class": {
            "doujinshell": "DoujinsHellSource",
            "dragontranslationorg": "DragonTranslationOrgSource",
            "emperorscan": "EmperorScanSource",
            "esmi2manga": "EsMi2MangaSource",
            "haremdekira": "HaremDeKiraSource",
            "infrafandub": "InfraFandubSource",
            "mangacrab": "MangaCrabSource",
            "mangasnosekai": "MangasNoSekaiSource",
            "manhuaonline": "SamuraiScanSource",
            "manhwalatino": "ManhwaLatinoSource",
        }.get(module.name, "MadaraSource"),
        "content_warning": _extract_kotlin_metadata(module),
    }


def _supported_mangathemesia(
    module: Path,
    build: str,
    language_override: str = "",
) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"mangathemesia"', build):
        return None
    if len(re.findall(r"\bsource\s*\{", build)) != 1:
        return None
    kotlin = "\n".join(path.read_text(encoding="utf-8") for path in module.rglob("*.kt"))
    custom_profiles = {
        "Luvyaa",
        "areamanga",
        "astralscans",
        "athreascans",
        "bloomscans",
        "comicasura",
        "drakescans",
        "elftoon",
        "erosscans",
        "evascans",
        "gafeland",
        "hadesscans",
        "hentaidex",
        "hijala",
        "kiwiyascans",
        "komikhwa",
        "kuromanga",
        "lavascans",
        "madarascans",
        "mangacan",
        "mangakimi",
        "mangasusu",
        "mangastop",
        "mangatv",
        "manhuascanus",
        "manhwaindo",
        "miauscan",
        "nexcomic",
        "ngomik",
        "nikatoons",
        "noxenscans",
        "pointzerotoons",
        "ragescans",
        "raindropfansub",
        "razure",
        "rizzcomic",
        "rokaricomics",
        "shojoscans",
        "skymangas",
        "soulscans",
        "starlightscan",
        "sushiscan",
        "thunderscans",
        "tsundokutraducoes",
        "witchscans",
    }
    functions = set(re.findall(r"override\s+fun\s+(\w+)\s*\(", kotlin))
    if functions - IGNORED_MANGATHEMESIA_FUNCTIONS and module.name not in custom_profiles:
        return None
    overrides = set(
        re.findall(
            r"override\s+(?:(?:protected|public|private)\s+)?(?:lateinit\s+)?val\s+(\w+)",
            kotlin,
        )
    )
    if overrides - SUPPORTED_MANGATHEMESIA_OVERRIDES and module.name not in custom_profiles:
        return None
    page_selector = _match(r'pageSelector[^=]*=\s*"([^"]+)"', kotlin)
    if (
        "pageSelector" in overrides
        and not re.search(r"#readerarea\b", page_selector, re.I)
        and module.name not in custom_profiles
    ):
        return None

    source = _source_block(build)
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source) or _match(
        r'custom\("(https?://[^"]+)"\)',
        source,
    )
    language = language_override or _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', source) or _match(
        r'\bname\s*=\s*"([^"]+)"', build
    )
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
        "manga_directory": _match(
            r'mangaUrlDirectory\s*(?::\s*String)?\s*=\s*"([^"]+)"',
            kotlin,
            "/manga",
        ),
        "reader_id": _match(r"#([A-Za-z][\w-]*)\s+img", page_selector, "readerarea"),
        "supports_latest": not bool(re.search(r"supportsLatest\s*=\s*false", kotlin)),
        "rpm": int(_match(r"\.rateLimit\(\s*(\d+)", kotlin, "1")) * 60,
        "image_no_referer_hosts": ("kumacdn",) if module.name == "culturedworks" else (),
        "search_profile": {
            "bloomscans": "sushi",
            "comicasura": "comic_asura",
            "hentaidex": "s",
            "mangacan": "mangacan",
            "mangatv": "s",
            "manhuascanus": "search",
            "ngomik": "ngomik",
            "rizzcomic": "rizz",
            "rokaricomics": "rokari",
            "starlightscan": "starlight",
            "sushiscan": "sushi",
        }.get(module.name, "default"),
        "browse_profile": {
            "rizzcomic": "rizz",
            "rokaricomics": "rokari",
        }.get(module.name, "default"),
        "chapter_profile": "astral" if module.name == "astralscans" else "default",
        "pages_profile": {
            "areamanga": "area_api",
            "bloomscans": "bloom",
            "comicasura": "all_images",
            "mangakimi": "mangakimi",
            "mangastop": "no_mihon",
            "mangatv": "mangatv",
            "soulscans": "no_gif",
        }.get(module.name, "default"),
        "reader_class": {
            "mangacan": "images",
            "starlightscan": "scanImagesContainer",
        }.get(module.name, ""),
        "image_class": {
            "comicasura": "object-cover",
            "starlightscan": "scanImage",
        }.get(module.name, ""),
        "page_element_classes": (
            ("pagination", "legendary-pagination", "magma-pagination")
            if module.name == "madarascans"
            else ()
        ),
        "request_referer": (
            base_url
            + _match(
                r'mangaUrlDirectory\s*(?::\s*String)?\s*=\s*"([^"]+)"',
                kotlin,
                "/manga",
            )
            if module.name == "sushiscan"
            else ""
        ),
        "accept_language": {
            "manhwaindo": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
            "mangastop": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        }.get(module.name, ""),
        "project_directory": (
            _match(r'projectPageString\s*=\s*"([^"]+)"', kotlin, "/project")
            if re.search(r"hasProjectPage\s*=\s*true", kotlin)
            else ""
        ),
        "date_format": _match(r'dateFormat\s*=\s*SimpleDateFormat\("([^"]+)"', kotlin, "MMMM dd, yyyy"),
        "date_locale": _match(r'SimpleDateFormat\([^\n]+Locale\("([^"]+)"\)', kotlin, "en"),
        "adapter_class": "MangaTVSource" if module.name == "mangatv" else "MangaThemesiaSource",
        "content_warning": _extract_kotlin_metadata(module),
    }


def _supported_pizzareader(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"pizzareader"', build):
        return None
    if len(re.findall(r"\bsource\s*\{", build)) != 1:
        return None
    source = _source_block(build)
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source) or _match(
        r'custom\("(https?://[^"]+)"\)',
        source,
    )
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', source) or _match(
        r'\bname\s*=\s*"([^"]+)"', build
    )
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None
    kotlin = "\n".join(path.read_text(encoding="utf-8") for path in module.rglob("*.kt"))
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
        "api_path": _match(r'apiPath\s*(?::\s*String)?\s*=\s*"([^"]+)"', kotlin, "/api"),
        "rpm": int(_match(r"\.rateLimit\(\s*(\d+)", kotlin, "1")) * 60,
    }


def _supported_mangacatalog(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"mangacatalog"', build):
        return None
    if len(re.findall(r"\bsource\s*\{", build)) != 1:
        return None
    source = _source_block(build)
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source) or _match(
        r'custom\("(https?://[^"]+)"\)',
        source,
    )
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', source) or _match(
        r'\bname\s*=\s*"([^"]+)"', build
    )
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    kotlin = "\n".join(path.read_text(encoding="utf-8") for path in module.rglob("*.kt"))
    kotlin = "\n".join(line for line in kotlin.splitlines() if not line.lstrip().startswith("//"))
    pairs = [
        (name, f"{base_url}{path}")
        for name, path in re.findall(
            r'Pair\("([^"]+)",\s*"\$baseUrl([^"]+)"\)',
            kotlin,
        )
    ]
    if not all((base_url, language, display_name, pairs)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
        "source_list": tuple(pairs),
    }


def _supported_masonry(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"masonry"', build):
        return None
    if len(re.findall(r"\bsource\s*\{", build)) != 1:
        return None
    source = _source_block(build)
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source)
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', source) or _match(
        r'\bname\s*=\s*"([^"]+)"', build
    )
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
    }


def _supported_iken(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"iken"', build):
        return None
    if len(re.findall(r"\bsource\s*\{", build)) != 1:
        return None
    source = _source_block(build)
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source)
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', source) or _match(
        r'\bname\s*=\s*"([^"]+)"', build
    )
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None
    kotlin = "\n".join(path.read_text(encoding="utf-8") for path in module.rglob("*.kt"))
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
        "per_page": int(_match(r"perPage\s*(?::\s*Int)?\s*=\s*(\d+)", kotlin, "18")),
        "sort_pages": bool(re.search(r"sortPagesByFilename\s*=\s*true", kotlin)),
        "chapters_api": bool(re.search(r"useChaptersApi\s*=\s*true", kotlin)),
        "content_warning": _extract_kotlin_metadata(module),
    }


def _supported_keyoapp(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"keyoapp"', build):
        return None
    if len(re.findall(r"\bsource\s*\{", build)) != 1:
        return None
    kotlin = "\n".join(path.read_text(encoding="utf-8") for path in module.rglob("*.kt"))
    functions = set(re.findall(r"override\s+fun\s+(\w+)\s*\(", kotlin))
    harmless = {
        "latestUpdatesSelector",
        "mangaDetailsParse",
        "popularMangaFromElement",
        "popularMangaSelector",
        "searchMangaSelector",
    }
    if functions - harmless and module.name not in {
        "artlapsa",
        "ritharscans",
        "suryascans",
        "timelesstoons",
    }:
        return None
    source = _source_block(build)
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source)
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', source) or _match(
        r'\bname\s*=\s*"([^"]+)"', build
    )
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
        "rpm": int(_match(r"\.rateLimit\(\s*(\d+)", kotlin, "1")) * 60,
        "search_profile": {
            "artlapsa": "artlapsa",
            "ritharscans": "rithar",
            "timelesstoons": "timeless",
        }.get(module.name, "default"),
        "popular_profile": {
            "suryascans": "search",
            "timelesstoons": "all_groups",
        }.get(module.name, "default"),
        "pages_profile": (
            "ld_json" if module.name in {"artlapsa", "ritharscans"} else "default"
        ),
    }


def _supported_foolslide(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"foolslide"', build):
        return None
    if len(re.findall(r"\bsource\s*\{", build)) != 1:
        return None
    kotlin = "\n".join(path.read_text(encoding="utf-8") for path in module.rglob("*.kt"))
    if re.search(r"override\s+fun\s+", kotlin) and module.name != "juinjutsuteamreader":
        return None
    source = _source_block(build)
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source) or _match(
        r'custom\("(https?://[^"]+)"\)',
        source,
    )
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', source) or _match(
        r'\bname\s*=\s*"([^"]+)"', build
    )
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
        "url_modifier": _match(
            r'urlModifier\s*(?::\s*String)?\s*=\s*"([^"]*)"',
            kotlin,
        ),
        "profile": "juinjutsu" if module.name == "juinjutsuteamreader" else "default",
        "content_warning": _extract_kotlin_metadata(module),
    }


def _supported_comiciviewer(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"comiciviewer"', build):
        return None
    source = _source_block(build)
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source)
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', source) or _match(r'\bname\s*=\s*"([^"]+)"', build)
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
        "supports_latest": module.name not in {"jnbooks", "magkan"},
        "latest_path": "/category/manga/{page}" if module.name in {"comicmedu", "mangabang", "rimacomiplus"} else "",
    }


def _supported_wpcomics(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"wpcomics"', build):
        return None
    source = _source_block(build)
    kotlin = "\n".join(path.read_text(encoding="utf-8") for path in module.rglob("*.kt"))
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source) or _match(
        r'custom\("(https?://[^"]+)"\)',
        source,
    )
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', source) or _match(r'\bname\s*=\s*"([^"]+)"', build)
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
        "popular_path": _match(r'popularPath[^=]*=\s*"([^"]+)"', kotlin, "hot"),
        "search_path": _match(r'searchPath[^=]*=\s*"([^"]+)"', kotlin, "tim-truyen"),
        "latest_path": "comic-update" if module.name == "xoxocomics" else "",
    }


def _supported_gigaviewer(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"gigaviewer"', build):
        return None
    source = _source_block(build)
    kotlin = "\n".join(path.read_text(encoding="utf-8") for path in module.rglob("*.kt"))
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source)
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', source) or _match(r'\bname\s*=\s*"([^"]+)"', build)
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
        "supports_latest": not bool(re.search(r"supportsLatest[^=]*=\s*false", kotlin)),
    }


def _supported_generic(
    module: Path,
    build: str,
    source_override: str = "",
) -> dict[str, object] | None:
    source = source_override or _source_block(build)
    kotlin = "\n".join(path.read_text(encoding="utf-8") for path in module.rglob("*.kt"))
    base_url_name = _match(r"\bbaseUrl\s*=\s*(\w+)", source)
    base_url = (
        _match(r'baseUrl\s*(?::[^=]+)?=\s*"(https?://[^"]+)"', source)
        or _match(r'custom\("(https?://[^"]+)"\)', source)
        or _match(r'mirrors\([\s\S]*?"(https?://[^"]+)"', source)
        or (
            _match(rf'\bval\s+{re.escape(base_url_name)}\s*=\s*"(https?://[^"]+)"', build)
            if base_url_name
            else ""
        )
        or _match(r'(?:override\s+)?val\s+baseUrl\s*(?::[^=]+)?=\s*"(https?://[^"]+)"', kotlin)
        or _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', kotlin)
    )
    language = _match(r'lang\s*=\s*"([^"]+)"', source) or module.parent.name
    display_name = (
        _match(r'\bname\s*=\s*"([^"]+)"', source)
        or _match(r'\bname\s*=\s*"([^"]+)"', build)
        or module.name
    )
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not base_url:
        return None
    extension = {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
        "rpm": int(_match(r"\.rateLimit\(\s*(\d+)", kotlin, "1")) * 60,
    }
    if module.name == "ikigaimangas":
        filters = next(module.rglob("Filters.kt")).read_text(encoding="utf-8")
        extension.update({
            "sort_options": tuple((value, name) for name, value in re.findall(
                r'SortProperty\("([^"]+)",\s*"([^"]+)"\)', filters,
            )),
            "status_options": tuple((value, name) for name, value in re.findall(
                r'Status\("([^"]+)",\s*(\d+)L\)', filters,
            )),
            "genre_options": tuple((value, name) for name, value in re.findall(
                r'Genre\("([^"]+)",\s*(\d+)L\)', filters,
            )),
        })
    elif module.name == "ikuhentai":
        filters = next(module.rglob("Filters.kt")).read_text(encoding="utf-8")
        sort_section = filters.partition("class SortBy")[2].partition("class Genre")[0]
        extension.update({
            "sort_options": tuple((value, name) for name, value in re.findall(
                r'Pair\("([^"]+)",\s*"([^"]*)"\)', sort_section,
            )),
            "status_options": tuple((value, name) for name, value in re.findall(
                r'Status\("([^"]+)",\s*"([^"]+)"\)', filters,
            )),
            "genre_options": tuple((value, name) for name, value in re.findall(
                r'Genre\("([^"]+)",\s*"([^"]+)"\)', filters,
            )),
        })
    elif module.name == "lectorjpg":
        extension["genre_options"] = tuple((value, name) for name, value in re.findall(
            r'Genre\("([^"]+)",\s*"([^"]+)"\)', kotlin,
        ))
    elif module.name == "leercapitulo":
        filters = next(module.rglob("Filters.kt")).read_text(encoding="utf-8")

        def options(name: str, next_name: str = "") -> tuple[tuple[str, str], ...]:
            block = filters.partition(f"class {name}")[2]
            if next_name:
                block = block.partition(f"class {next_name}")[0]
            return tuple((value, label) for label, value in re.findall(r'Pair\("([^"]+)",\s*"([^"]*)"\)', block))

        extension.update({
            "rpm": 20,
            "genre_options": options("GenreFilter", "AlphabeticFilter"),
            "alphabet_options": options("AlphabeticFilter", "StatusFilter"),
            "status_options": options("StatusFilter", "UriPartFilter"),
        })
    elif module.name == "leermangaesp":
        filters = next(module.rglob("Filters.kt")).read_text(encoding="utf-8")
        extension.update({
            "genre_options": tuple((value, name) for name, value in re.findall(r'Genre\("([^"]+)",\s*"([^"]+)"\)', filters)),
            "type_options": tuple((value, name) for name, value in re.findall(r'Pair\("([^"]+)",\s*"([^"]*)"\)', filters)),
        })
    elif module.name == "lmtoonline":
        filters = next(module.rglob("Filters.kt")).read_text(encoding="utf-8")

        def filter_block(name: str) -> str:
            start = filters.find("listOf(", filters.find(f"{name}(", filters.find("fun getFilters")))
            if start < 0:
                return ""
            start += len("listOf(")
            depth = 1
            for index, char in enumerate(filters[start:], start):
                depth += (char == "(") - (char == ")")
                if depth == 0:
                    return filters[start:index]
            return ""

        extension.update({
            "genre_options": tuple((value, value) for value in re.findall(r'"([^"]+)"', filter_block("GenreFilter"))),
            **{
                f"{name}_options": tuple((value, label) for label, value in re.findall(r'Pair\("([^"]+)",\s*"([^"]*)"\)', filter_block(f"{name.capitalize()}Filter")))
                for name in ("status", "demographic", "type", "nsfw", "order")
            },
        })
    elif module.name == "mangamx":
        filters = next(module.rglob("Filters.kt")).read_text(encoding="utf-8")

        def options(name: str, next_name: str) -> tuple[tuple[str, str], ...]:
            block = filters.partition(f"class {name}")[2].partition(f"class {next_name}")[0]
            return tuple((value, label) for label, value in re.findall(r'Pair\("([^"]+)",\s*"([^"]+)"\)', block))

        extension.update({
            "status_options": options("StatusFilter", "TypeFilter"),
            "type_options": options("TypeFilter", "GenreFilter"),
            "genre_options": options("GenreFilter", "AdultContentFilter"),
            "sort_options": (("visitas", "Visitas"), ("id", "Recientes"), ("nombre", "Alfabético")),
        })
    return extension


def _supported_heavenmanga(module: Path, build: str) -> dict[str, object] | None:
    extension = _supported_generic(module, build)
    if extension is None:
        return None
    filters = next(module.rglob("Filters.kt")).read_text(encoding="utf-8")

    def options(start: str, end: str = "") -> tuple[tuple[str, str], ...]:
        section = filters.partition(f"class {start}")[2]
        if end:
            section = section.partition(f"class {end}")[0]
        return tuple(re.findall(r'Pair\("([^"]+)",\s*"([^"]*)"\)', section))

    extension.update({
        "genre_options": options("GenreFilter", "AlphabeticoFilter"),
        "alphabet_options": options("AlphabeticoFilter", "ListaCompletasFilter"),
        "list_options": options("ListaCompletasFilter"),
    })
    return extension


def _supported_hentaihall(module: Path, build: str) -> dict[str, object] | None:
    extension = _supported_generic(module, build)
    if extension is None:
        return None
    filters = next(module.rglob("Filters.kt")).read_text(encoding="utf-8")
    genre_block = _match(r"GENRES\s*=\s*listOf\(([\s\S]*?)\)\s*$", filters)
    extension["genres"] = tuple(re.findall(r'"([^"]+)"', genre_block))
    return extension


def _supported_mangadex(module: Path, build: str, language: str) -> dict[str, object]:
    aliases = {
        "es": ("es-la", "es"),
        "es-419": ("es-la",),
        "zh-Hans": ("zh-hans",),
        "zh-Hant": ("zh-hk",),
        "pt-BR": ("pt-br",),
    }
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": f"MangaDex ({language})",
        "version": f"0.{_match(r'versionCode\s*=\s*(\d+)', build, '1')}.0",
        "language": language,
        "languages": aliases.get(language, (language.lower(),)),
    }


def _supported_zeistmanga(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"zeistmanga"', build):
        return None
    if len(re.findall(r"\bsource\s*\{", build)) != 1:
        return None
    kotlin = "\n".join(path.read_text(encoding="utf-8") for path in module.rglob("*.kt"))
    functions = set(re.findall(r"override\s+fun\s+(\w+)", kotlin))
    ignored_functions = {
        "getGenreList",
        "getStatusList",
        "getTypeList",
        "mangaDetailsParse",
    }
    popular_is_latest = module.name in {
        "apenasumafa",
        "darkroomfansub",
        "mangahub",
        "mikoroku",
        "murim",
        "okyykomik",
        "pinkrosa",
        "shiyurasub",
        "traducoesdolipe",
        "xsanomanga",
    }
    allowed_functions = ignored_functions | {"getChapterFeedUrl"}
    if popular_is_latest:
        allowed_functions |= {"popularMangaParse", "popularMangaRequest"}
    if module.name == "yaoifanclub":
        allowed_functions.add("headersBuilder")
    custom_profiles = {
        "apenasumafa",
        "gistamishouse",
        "hanmokkuscan",
        "inazumanga",
        "mikoroku",
        "mikrokosmosfansub",
        "murimscan",
        "osakascan",
        "pinkrosa",
        "sapphirescan",
        "shadowceviri",
        "tooncubus",
        "traducoesdolipe",
        "ulascomic",
        "yokai",
    }
    if functions - allowed_functions and module.name not in custom_profiles:
        return None
    source = _source_block(build)
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source)
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', source) or _match(
        r'\bname\s*=\s*"([^"]+)"', build
    )
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
        "manga_category": _match(
            r'mangaCategory\s*(?::\s*String)?\s*=\s*"([^"]+)"',
            kotlin,
            "Series",
        ),
        "chapter_category": _match(
            r'chapterCategory\s*(?::\s*String)?\s*=\s*"([^"]*)"',
            kotlin,
            "Chapter",
        ),
        "new_feed": bool(re.search(r"useNewChapterFeed\s*=\s*true", kotlin)),
        "chapter_feed_profile": {
            "apenasumafa": "data_label",
            "comicverse": "comicverse",
            "murimscan": "og_title",
            "pinkrosa": "data_label",
            "traducoesdolipe": "cat_name",
            "ulascomic": "title",
            "yurimoonsub": "yurimoon",
        }.get(module.name, "default"),
        "popular_is_latest": popular_is_latest,
        "popular_profile": {
            "gistamishouse": "gistamis",
            "inazumanga": "pop_card",
            "shadowceviri": "gallery",
            "ulascomic": "serieslist",
        }.get(module.name, "default"),
        "request_referer": (
            f"https://www.blogger.com/blogin.g?blogspotURL={base_url}/&type=blog&bpli=1"
            if module.name == "yaoifanclub"
            else ""
        ),
        "search_profile": "hanmokku" if module.name == "hanmokkuscan" else "default",
        "chapter_profile": {
            "osakascan": "number_desc",
            "tooncubus": "html_list",
            "yokai": "yokai",
        }.get(module.name, "default"),
        "chapter_categories": ("Capitulo", "Cap") if module.name == "gistamishouse" else (),
        "old_feed": bool(re.search(r"useOldChapterFeed\s*=\s*true", kotlin)),
        "pages_profile": {
            "darkroomfansub": "reader_separators",
            "datgarscanlation": "check_box_separators",
            "gistamishouse": "article_images",
            "inazumanga": "textarea_raw",
            "mikoroku": "broad_separators",
            "mikrokosmosfansub": "template_html",
            "pinkrosa": "separator_links",
            "traducoesdolipe": "json_array",
            "ulascomic": "ulas_script",
        }.get(module.name, "default"),
        "latest_order": "updated" if module.name == "ulascomic" else "published",
        "strip_series_query": module.name == "murimscan",
        "supports_latest": not bool(re.search(r"supportsLatest\s*=\s*false", kotlin)),
        "requests_per_minute": int(_match(r"\.rateLimit\((\d+)\s*\)", kotlin, "1")) * 60
        if ".rateLimit(" in kotlin else 60,
        "has_filters": bool(re.search(r"hasFilters\s*=\s*true", kotlin)),
        "has_language_filter": not bool(re.search(r"hasLanguageFilter\s*=\s*false", kotlin)),
        "excluded_categories": (
            ("Anime", "Novela") if module.name == "gistamishouse" else ("Anime",)
        ),
        "status_filters": (
            (("Activo", "Activo"), ("Completo", "Completo"), ("Cancelado", "Cancelado"),
             ("Futuro", "Futuro"), ("Pausado", "Pausado"))
            if module.name == "gistamishouse" else ()
        ),
        "type_filters": (
            (("Manga", "Manga"), ("Manhua", "Manhua"), ("Manhwa", "Manhwa"))
            if module.name == "gistamishouse" else ()
        ),
        "genre_filters": (
            tuple((value, value) for value in (
                "Acción", "Aventura", "Comedia", "Dementia", "Demonios", "Drama", "Ecchi",
                "Fantasía", "Videojuegos", "Harem", "Histórico", "Horror", "Josei", "Magia",
                "Arte marcial", "Mecha", "Militar", "Música", "Misterio", "Parody", "Policia",
                "Filosófico", "Romance", "Samurai", "Escolar", "Sci-Fi", "Seinen", "Shoujo",
                "GL", "BL", "HET", "Shounen", "Vida cotidiana", "Espacio", "Deportes",
                "Super poderes", "Sobrenatural", "Thriller", "Vampiro", "Vida laboral",
            )) if module.name == "gistamishouse" else ()
        ),
        "details_profile": "gistamis" if module.name == "gistamishouse" else "default",
        "content_warning": _extract_kotlin_metadata(module),
        "paginate_chapter_feed": module.name == "datgarscanlation",
    }


def _supported_guya(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"guya"', build):
        return None
    source = _source_block(build)
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source)
    language = _match(r'lang\s*=\s*"([^"]+)"', source, module.parent.name)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', source) or _match(
        r'\bname\s*=\s*"([^"]+)"', build
    )
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
    }


def _supported_grouple(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"grouple"', build):
        return None
    kotlin = "\n".join(path.read_text(encoding="utf-8") for path in module.rglob("*.kt"))
    functions = set(re.findall(r"override\s+fun\s+(\w+)\s*\(", kotlin))
    if functions - {"chapterScanlatorFromElement", "getFilterList", "mangaDetailsParse"}:
        return None
    source = _source_block(build)
    base_url = _match(r'custom\("(https?://[^"]+)"\)', source) or _match(
        r'baseUrl\s*=\s*"(https?://[^"]+)"',
        source,
    )
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', source) or _match(
        r'\bname\s*=\s*"([^"]+)"', build
    )
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
    }


def _supported_manga18(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"manga18"', build):
        return None
    source = _source_block(build)
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source)
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', source) or _match(
        r'\bname\s*=\s*"([^"]+)"', build
    )
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
    }


def _supported_manhwaz(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"manhwaz"', build):
        return None
    source = _source_block(build)
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source) or _match(
        r'custom\("(https?://[^"]+)"\)',
        source,
    )
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', source) or _match(
        r'\bname\s*=\s*"([^"]+)"', build
    )
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None
    kotlin = "\n".join(path.read_text(encoding="utf-8") for path in module.rglob("*.kt"))
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
        "search_path": _match(
            r'searchPath\s*(?::\s*String)?\s*=\s*"([^"]+)"',
            kotlin,
            "search",
        ),
        "popular_catalog_path": "genre/manhwa" if module.name == "manhwaz" else "",
    }


def _supported_madtheme(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"madtheme"', build):
        return None
    source = _source_block(build)
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source) or _match(
        r'mirrors\(\s*"(https?://[^"]+)"',
        source,
    )
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', source) or _match(
        r'\bname\s*=\s*"([^"]+)"', build
    )
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None
    kotlin = "\n".join(path.read_text(encoding="utf-8") for path in module.rglob("*.kt"))
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
        "legacy_api": bool(re.search(r"useLegacyApi\s*=\s*true", kotlin)),
        "slug_search": bool(re.search(r"useSlugSearch\s*=\s*true", kotlin)),
    }


def _supported_natsuid(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"natsuid"', build):
        return None
    source = _source_block(build)
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source)
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', source) or _match(
        r'\bname\s*=\s*"([^"]+)"', build
    )
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
        "chapter_page": "1" if module.name == "kiryuu" else "999",
    }


def _supported_liliana(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"liliana"', build):
        return None
    source = _source_block(build)
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source) or _match(
        r'custom\("(https?://[^"]+)"\)',
        source,
    )
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', source) or _match(
        r'\bname\s*=\s*"([^"]+)"', build
    )
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
        "profile": "dokiraw" if module.name == "dokiraw" else "default",
    }


def _supported_mangareader(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"mangareader"', build):
        return None
    source = _source_block(build)
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source) or _match(
        r'custom\("(https?://[^"]+)"\)',
        source,
    )
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', source) or _match(
        r'\bname\s*=\s*"([^"]+)"', build
    )
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None
    japanese_api = module.name in {"klraw", "mangamura", "rawotaku"}
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
        "search_at_root": japanese_api or module.name == "jmanga",
        "search_keyword": "q" if japanese_api or module.name == "jmanga" else "keyword",
        "page_parameter": "p" if japanese_api else "page",
        "chapter_container_id": "ja-chaps" if japanese_api else "en-chapters",
        "ajax_kind": "json" if japanese_api else "default",
        "exclude_placeholder": module.name == "manganow",
    }


def _supported_uzaymanga(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"uzaymanga"', build):
        return None
    source = _source_block(build)
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source)
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', source) or _match(
        r'\bname\s*=\s*"([^"]+)"', build
    )
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    kotlin = "\n".join(path.read_text(encoding="utf-8") for path in module.rglob("*.kt"))
    cdn_url = _match(r'cdnUrl[^=]*=\s*"(https?://[^"]+)"', kotlin)
    if not all((base_url, language, display_name)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
        "cdn_url": cdn_url.rstrip("/"),
    }


def _supported_colorlibanime(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"colorlibanime"', build):
        return None
    source = _source_block(build)
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source)
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', source) or _match(
        r'\bname\s*=\s*"([^"]+)"', build
    )
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
    }


def _supported_bakkin(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"bakkin"', build):
        return None
    source = _source_block(build)
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source) or _match(
        r'custom\("(https?://[^"]+)"\)',
        source,
    )
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', source) or _match(
        r'\bname\s*=\s*"([^"]+)"', build
    )
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/") + "/",
        "language": language,
    }


def _supported_mangaworld(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"mangaworld"', build):
        return None
    source = _source_block(build)
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source)
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', source) or _match(
        r'\bname\s*=\s*"([^"]+)"', build
    )
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
    }


def _supported_oceanwp(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"oceanwp"', build):
        return None
    source = _source_block(build)
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source)
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', source) or _match(
        r'\bname\s*=\s*"([^"]+)"', build
    )
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
    }


def _supported_monochrome(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"monochrome"', build):
        return None
    source = _source_block(build)
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source) or _match(
        r'custom\("(https?://[^"]+)"\)',
        source,
    )
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', source) or _match(
        r'\bname\s*=\s*"([^"]+)"', build
    )
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
        "api_url": (
            "https://api-3qnqyl7llq-lz.a.run.app"
            if module.name == "monochromecustom"
            else base_url.replace("://", "://api.", 1).rstrip("/")
        ),
    }


def _supported_multichan(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"multichan"', build):
        return None
    source = _source_block(build)
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source) or _match(
        r'custom\("(https?://[^"]+)"\)',
        source,
    )
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', source) or _match(
        r'\bname\s*=\s*"([^"]+)"', build
    )
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
        "profile": "henchan" if module.name == "henchan" else "regular",
    }


def _supported_goda(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"goda"', build):
        return None
    source = _source_block(build)
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source) or _match(
        r'mirrors\(\s*"(https?://[^"]+)"',
        source,
    )
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', source) or _match(
        r'\bname\s*=\s*"([^"]+)"', build
    )
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
        "profile": "api" if module.name == "baozimhorg" else "regular",
    }


def _supported_gattsu(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"gattsu"', build):
        return None
    source = _source_block(build)
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source)
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', build)
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
        "profile": "universo" if module.name == "universohentai" else "regular",
    }


def _supported_moonlighttl(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"moonlighttl"', build):
        return None
    source = _source_block(build)
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source)
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', build)
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
        "profile": "asteria" if module.name == "lectorasteria" else "regular",
        "content_warning": _extract_kotlin_metadata(module),
    }


def _supported_scanreader(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"scanreader"', build):
        return None
    source = _source_block(build)
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source)
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', build)
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
    }


def _supported_heancms(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"heancms"', build):
        return None
    source = _source_block(build)
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source)
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', build)
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
        "new_query": module.name == "omegascans",
        "latest_order": "asc" if module.name == "luascans" else "desc",
    }


def _supported_fuzzydoodle(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"fuzzydoodle"', build):
        return None
    source = _source_block(build)
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source)
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', source) or _match(r'\bname\s*=\s*"([^"]+)"', build)
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
        "latest_profile": "home" if module.name == "lelscanvf" else "manga",
    }


def _supported_spicytheme(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"spicytheme"', build):
        return None
    source = _source_block(build)
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source)
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', build)
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
        "api_base_url": "https://back.spicyseries.com" if module.name == "spicyscan" else "",
    }


def _supported_mangadventure(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"mangadventure"', build):
        return None
    source = _source_block(build)
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source)
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', build)
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
    }


def _supported_mangawork(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"mangawork"', build):
        return None
    source = _source_block(build)
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source)
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', build)
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
        "series_path": "todas-as-obras" if module.name == "pizzariascan" else "series",
    }


def _supported_ezmanhwa(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"ezmanhwa"', build):
        return None
    source = _source_block(build)
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source)
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', source) or _match(r'\bname\s*=\s*"([^"]+)"', build)
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
        "api_url": "https://vapi.ezmanga.org/api/v1" if module.name == "ezmanga" else "https://api.qimanga.com/api/v1",
    }


def _supported_fansubscat(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"fansubscat"', build):
        return None
    source = _source_block(build)
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source)
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', build)
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
    }


def _supported_kemono(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"kemono"', build):
        return None
    source = _source_block(build)
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source)
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', build)
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
    }


def _supported_mangataro(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"mangataro"', build):
        return None
    source = _source_block(build)
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source)
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', build)
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
    }


def _supported_mangabox(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"mangabox"', build):
        return None
    source = _source_block(build)
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source) or _match(
        r'mirrors\(\s*"(https?://[^"]+)"', source
    )
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', build)
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
        "chapter_profile": "kakalot" if module.name == "mangakakalot" else "regular",
    }


def _supported_fmreader(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"fmreader"', build):
        return None
    source = _source_block(build)
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source)
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', build)
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    profiles = {
        "welovemangaone": "love",
        "rawlh": "welove",
        "rawinu": "rawinu",
        "mangagun": "nihon",
    }
    if not all((base_url, language, display_name)) or module.name not in profiles:
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
        "profile": profiles[module.name],
    }


def _supported_stalkercms(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"stalkercms"', build):
        return None
    source = _source_block(build)
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source)
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', build)
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
    }


def _supported_vercomics(module: Path, build: str) -> list[dict[str, object]]:
    if not re.search(r'theme\s*=\s*"vercomics"', build):
        return []
    module_name = _match(r'\bname\s*=\s*"([^"]+)"', build)
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    result: list[dict[str, object]] = []
    for source in _source_blocks(build):
        base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source)
        language = _match(r'lang\s*=\s*"([^"]+)"', source)
        display_name = _match(r'\bname\s*=\s*"([^"]+)"', source) or module_name
        if not all((base_url, language, display_name)):
            continue
        key = display_name.lower()
        result.append(
            {
                "id": f"{_slug(module.name if len(_source_blocks(build)) == 1 else display_name)}_{_slug(language)}",
                "name": display_name,
                "version": f"0.{version}.0",
                "base_url": base_url.rstrip("/"),
                "language": language,
                "url_suffix": "comics-porno" if key == "vcp" else "xxx" if key == "vmp" else "porno",
                "use_suffix_on_search": module.name != "chochox",
            }
        )
    return result


def _supported_senkuro(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"senkuro"', build):
        return None
    source = _source_block(build)
    mirrors = re.findall(r'"(https?://[^"]+)"', source)
    base_url = mirrors[-1] if mirrors else ""
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', build)
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
    }


def _supported_hiper(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"hiper"', build):
        return None
    source = _source_block(build)
    base_url = _match(r'(?:baseUrl\s*=\s*|custom\()"((?:https?://)[^"]+)"', source)
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', source) or _match(
        r'\bname\s*=\s*"([^"]+)"', build
    )
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    headers = {
        "hipercool": {"x-flux-node": "G2ZsDdWhUwdU82Vw"},
        "hiperdex": {"x-cfg-auth": "yceqt7qgu004"},
    }.get(module.name, {})
    if not all((base_url, language, display_name)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
        "headers": headers,
    }


def _supported_greenshit(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"greenshit"', build):
        return None
    source = _source_block(build)
    kotlin = "\n".join(path.read_text(encoding="utf-8") for path in module.rglob("*.kt"))
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source)
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', build)
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    api_url = _match(r'apiUrl\s*=\s*"(https?://[^"]+)"', kotlin)
    cdn_url = _match(r'cdnUrl\s*=\s*"(https?://[^"]+)"', kotlin)
    scan_id = _match(r'scanId\s*=\s*"([^"]+)"', kotlin)
    if not all((base_url, language, display_name, api_url, cdn_url, scan_id)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
        "api_url": api_url.rstrip("/"),
        "cdn_url": cdn_url.rstrip("/"),
        "scan_id": scan_id,
        "default_genre_id": _match(r'defaultGenreId\s*=\s*"([^"]+)"', kotlin, "1"),
    }


def _supported_libgroup(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"libgroup"', build):
        return None
    source = _source_block(build)
    kotlin = "\n".join(path.read_text(encoding="utf-8") for path in module.rglob("*.kt"))
    base_url = _match(r'(?:baseUrl\s*=\s*|custom\()"((?:https?://)[^"]+)"', source)
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', build)
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    site_id = _match(r'siteId\s*(?::\s*Int)?\s*=\s*(\d+)', kotlin)
    if not all((base_url, language, display_name, site_id)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
        "site_id": int(site_id),
    }


def _supported_mccms(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"mccms"', build):
        return None
    source = _source_block(build)
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source)
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', source) or _match(
        r'\bname\s*=\s*"([^"]+)"', build
    )
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
    }


def _supported_zmanga(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"zmanga"', build):
        return None
    source = _source_block(build)
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source)
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', build)
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
    }


def _supported_hentaihand(module: Path, build: str) -> list[dict[str, object]]:
    if not re.search(r'theme\s*=\s*"hentaihand"', build):
        return []
    languages_match = re.search(
        r"val\s+languages\s*=\s*listOf\((.*?)\)",
        build,
        re.DOTALL,
    )
    languages_block = languages_match.group(1) if languages_match else ""
    languages = re.findall(r'"([^"]+)"', languages_block, re.DOTALL)
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', build)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', build)
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    kotlin = "\n".join(path.read_text(encoding="utf-8") for path in module.rglob("*.kt"))
    id_map = {
        language: [int(value) for value in re.findall(r"\d+", values)]
        for language, values in re.findall(
            r'"([^"]+)"\s*->\s*listOf\(([^)]*)\)',
            kotlin,
        )
    }
    if not all((languages, base_url, display_name)):
        return []
    return [
        {
            "id": f"{_slug(module.name)}_{_slug(language)}",
            "name": f"{display_name} ({language})",
            "version": f"0.{version}.0",
            "base_url": base_url.rstrip("/"),
            "language": language,
            "language_ids": id_map.get(language, []),
        }
        for language in languages
    ]


def _supported_eromuse(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"eromuse"', build):
        return None
    source = _source_block(build)
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source)
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', build)
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
        "profile": "erofus" if module.name == "erofus" else "eightmuses",
    }


def _supported_galleryadults(module: Path, build: str) -> list[dict[str, object]]:
    if not re.search(r'theme\s*=\s*"galleryadults"', build):
        return []
    languages_match = re.search(r"listOf\((.*?)\)\.forEach\s*\{\s*language", build, re.DOTALL)
    languages = re.findall(r'"([^"]+)"', languages_match.group(1)) if languages_match else []
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', build)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', build)
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    language_names = {
        "en": "english",
        "ja": "japanese",
        "zh": "chinese",
        "es": "spanish",
        "fr": "french",
        "ko": "korean",
        "de": "german",
        "ru": "russian",
        "all": "",
    }
    if not all((languages, base_url, display_name)):
        return []
    return [
        {
            "id": f"{_slug(module.name)}_{_slug(language)}",
            "name": f"{display_name} ({language})",
            "version": f"0.{version}.0",
            "base_url": base_url.rstrip("/"),
            "language": language,
            "manga_language": language_names.get(language, language),
            "profile": module.name,
        }
        for language in languages
    ]


def _supported_mangahub(module: Path, build: str) -> dict[str, object] | None:
    if not re.search(r'theme\s*=\s*"mangahub"', build):
        return None
    source = _source_block(build)
    kotlin = "\n".join(path.read_text(encoding="utf-8") for path in module.rglob("*.kt"))
    base_url = _match(r'baseUrl\s*=\s*"(https?://[^"]+)"', source)
    language = _match(r'lang\s*=\s*"([^"]+)"', source)
    display_name = _match(r'\bname\s*=\s*"([^"]+)"', build)
    manga_source = _match(r'mangaSource\s*=\s*"([^"]+)"', kotlin)
    version = _match(r"versionCode\s*=\s*(\d+)", build, "1")
    if not all((base_url, language, display_name, manga_source)):
        return None
    return {
        "id": f"{_slug(module.name)}_{_slug(language)}",
        "name": display_name,
        "version": f"0.{version}.0",
        "base_url": base_url.rstrip("/"),
        "language": language,
        "manga_source": manga_source,
    }


def _madara_bundle(engine: str, extension: dict[str, object]) -> bytes:
    config = (
        f"\n\nclass GeneratedMadaraSource({extension['adapter_class']}):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        f"    manga_substring = {extension['manga_substring']!r}\n"
        f"    load_more = {extension['load_more']!r}\n"
        f"    use_new_chapter_endpoint = {extension['new_chapters']!r}\n"
        f"    chapter_url_suffix = {extension['chapter_url_suffix']!r}\n"
        f"    supports_latest = {extension['supports_latest']!r}\n"
        f"    requests_per_minute = {extension['rpm']!r}\n"
        f"    pages_profile = {extension['pages_profile']!r}\n"
        f"    extra_headers = {extension['extra_headers']!r}\n"
        f"    image_headers = {extension['image_headers']!r}\n"
        f"    date_format = {extension['date_format']!r}\n"
        f"    date_locale = {extension['date_locale']!r}\n"
        f"    details_profile = {extension['details_profile']!r}\n"
        f"    content_warning = {extension['content_warning']!r}\n"
        "\n\nSOURCE = GeneratedMadaraSource\n"
    )
    return (engine.rstrip() + config).encode()


def _mangathemesia_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        f"\n\nclass GeneratedMangaThemesiaSource({extension['adapter_class']}):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        f"    manga_directory = {extension['manga_directory']!r}\n"
        f"    reader_id = {extension['reader_id']!r}\n"
        f"    supports_latest = {extension['supports_latest']!r}\n"
        f"    requests_per_minute = {extension['rpm']!r}\n"
        f"    image_no_referer_hosts = {extension['image_no_referer_hosts']!r}\n"
        f"    search_profile = {extension['search_profile']!r}\n"
        f"    browse_profile = {extension['browse_profile']!r}\n"
        f"    chapter_profile = {extension['chapter_profile']!r}\n"
        f"    pages_profile = {extension['pages_profile']!r}\n"
        f"    reader_class = {extension['reader_class']!r}\n"
        f"    image_class = {extension['image_class']!r}\n"
        f"    page_element_classes = {extension['page_element_classes']!r}\n"
        f"    request_referer = {extension['request_referer']!r}\n"
        f"    accept_language = {extension['accept_language']!r}\n"
        f"    project_directory = {extension['project_directory']!r}\n"
        f"    date_format = {extension['date_format']!r}\n"
        f"    date_locale = {extension['date_locale']!r}\n"
        f"    content_warning = {extension['content_warning']!r}\n"
        "\n\nSOURCE = GeneratedMangaThemesiaSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _pizzareader_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedPizzaReaderSource(PizzaReaderSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        f"    api_path = {extension['api_path']!r}\n"
        f"    requests_per_minute = {extension['rpm']!r}\n"
        "\n\nSOURCE = GeneratedPizzaReaderSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _mangacatalog_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedMangaCatalogSource(MangaCatalogSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        f"    source_list = {extension['source_list']!r}\n"
        "\n\nSOURCE = GeneratedMangaCatalogSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _masonry_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedMasonrySource(MasonrySource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        "\n\nSOURCE = GeneratedMasonrySource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _iken_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedIkenSource(IkenSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        f"    per_page = {extension['per_page']!r}\n"
        f"    sort_pages_by_filename = {extension['sort_pages']!r}\n"
        f"    use_chapters_api = {extension['chapters_api']!r}\n"
        f"    content_warning = {extension['content_warning']!r}\n"
        "\n\nSOURCE = GeneratedIkenSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _keyoapp_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedKeyoappSource(KeyoappSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        f"    requests_per_minute = {extension['rpm']!r}\n"
        f"    search_profile = {extension['search_profile']!r}\n"
        f"    popular_profile = {extension['popular_profile']!r}\n"
        f"    pages_profile = {extension['pages_profile']!r}\n"
        "\n\nSOURCE = GeneratedKeyoappSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _foolslide_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedFoolSlideSource(FoolSlideSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        f"    url_modifier = {extension['url_modifier']!r}\n"
        f"    profile = {extension['profile']!r}\n"
        f"    content_warning = {extension['content_warning']!r}\n"
        "\n\nSOURCE = GeneratedFoolSlideSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _comiciviewer_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedComiciViewerSource(ComiciViewerSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    api_url = {extension['base_url'] + '/api'!r}\n"
        f"    language = {extension['language']!r}\n"
        f"    supports_latest = {extension['supports_latest']!r}\n"
        f"    latest_path = {extension['latest_path']!r}\n"
        "\n\nSOURCE = GeneratedComiciViewerSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _wpcomics_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedWPComicsSource(WPComicsSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        f"    popular_path = {extension['popular_path']!r}\n"
        f"    search_path = {extension['search_path']!r}\n"
        f"    latest_path = {extension['latest_path']!r}\n"
        "\n\nSOURCE = GeneratedWPComicsSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _gigaviewer_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedGigaViewerSource(GigaViewerSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        f"    supports_latest = {extension['supports_latest']!r}\n"
        "\n\nSOURCE = GeneratedGigaViewerSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _generic_bundle(
    common_engine: str,
    generic_engine: str,
    extension: dict[str, object],
) -> bytes:
    extension_id = str(extension["id"])
    adapter_class = (
        "DragonBallMultiverseSource" if extension_id.startswith("dragonballmultiverse_")
        else "DynastySource" if extension_id == "dynasty_es"
        else "EnchiladaScanSource" if extension_id == "enchiladascan_es"
        else "HentaiModeSource" if extension_id == "hentaimode_es"
        else "IkigaiMangasSource" if extension_id == "ikigaimangas_es"
        else "IkuhentaiSource" if extension_id == "ikuhentai_es"
        else "InMangaSource" if extension_id == "inmanga_es"
        else "InsanosScanSource" if extension_id == "insanosscan_es"
        else "JeazScansSource" if extension_id == "jeazscans_es"
        else "KoinoboriScanSource" if extension_id == "koinoboriscan_es"
        else "LeerCapituloSource" if extension_id == "leercapitulo_es"
        else "LeerMangaEspSource" if extension_id == "leermangaesp_es"
        else "LectorJpgSource" if extension_id == "lectorjpg_es"
        else "MangoLibreriaSource" if extension_id == "lectormonline_es"
        else "LmtosSource" if extension_id == "lmtoonline_es"
        else "MangaOniSource" if extension_id == "mangamx_es"
        else "MangasInSource" if extension_id == "mangasin_es"
        else "GenericSource"
    )
    extra = (
        f"    genre_options = {extension['genre_options']!r}\n"
        f"    alphabet_options = {extension['alphabet_options']!r}\n"
        f"    status_options = {extension['status_options']!r}\n"
        if extension_id == "leercapitulo_es"
        else (
            f"    type_options = {extension['type_options']!r}\n"
            f"    genre_options = {extension['genre_options']!r}\n"
        ) if extension_id == "leermangaesp_es"
        else (
            f"    genre_options = {extension['genre_options']!r}\n"
            f"    status_options = {extension['status_options']!r}\n"
            f"    demographic_options = {extension['demographic_options']!r}\n"
            f"    type_options = {extension['type_options']!r}\n"
            f"    nsfw_options = {extension['nsfw_options']!r}\n"
            f"    order_options = {extension['order_options']!r}\n"
        ) if extension_id == "lmtoonline_es"
        else (
            f"    status_options = {extension['status_options']!r}\n"
            f"    type_options = {extension['type_options']!r}\n"
            f"    genre_options = {extension['genre_options']!r}\n"
            f"    sort_options = {extension['sort_options']!r}\n"
        ) if extension_id == "mangamx_es"
        else f"    genre_options = {extension['genre_options']!r}\n" if extension_id == "lectorjpg_es" else (
        f"    sort_options = {extension['sort_options']!r}\n"
        f"    status_options = {extension['status_options']!r}\n"
        f"    genre_options = {extension['genre_options']!r}\n"
        if extension_id in {"ikigaimangas_es", "ikuhentai_es"}
        else ""
    ))
    config = (
        f"\n\nclass GeneratedGenericSource({adapter_class}):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        f"    requests_per_minute = {extension['rpm']!r}\n"
        f"    content_warning = {extension['content_warning']!r}\n"
        f"{extra}"
        "\n\nSOURCE = GeneratedGenericSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + generic_engine.rstrip() + config).encode()


def _heavenmanga_bundle(
    common_engine: str,
    heavenmanga_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedHeavenMangaSource(HeavenMangaSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        f"    requests_per_minute = {extension['rpm']!r}\n"
        f"    genre_options = {extension['genre_options']!r}\n"
        f"    alphabet_options = {extension['alphabet_options']!r}\n"
        f"    list_options = {extension['list_options']!r}\n"
        f"    content_warning = {extension['content_warning']!r}\n"
        "\n\nSOURCE = GeneratedHeavenMangaSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + heavenmanga_engine.rstrip() + config).encode()


def _hentaihall_bundle(
    common_engine: str,
    hentaihall_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedHentaiHallSource(HentaiHallSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        f"    requests_per_minute = {extension['rpm']!r}\n"
        f"    genres = {extension['genres']!r}\n"
        f"    content_warning = {extension['content_warning']!r}\n"
        "\n\nSOURCE = GeneratedHentaiHallSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + hentaihall_engine.rstrip() + config).encode()


def _mangadex_bundle(engine: str, extension: dict[str, object]) -> bytes:
    alias = "\nMangaDexEsSource = GeneratedMangaDexSource\n" if extension["language"] == "es" else ""
    config = (
        "\n\nclass GeneratedMangaDexSource(MangaDexSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    language = {extension['language']!r}\n"
        f"    languages = {extension['languages']!r}\n"
        f"{alias}"
        "\nSOURCE = GeneratedMangaDexSource\n"
    )
    return (engine.rstrip() + config).encode()


def _zeistmanga_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedZeistMangaSource(ZeistMangaSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        f"    manga_category = {extension['manga_category']!r}\n"
        f"    chapter_category = {extension['chapter_category']!r}\n"
        f"    use_new_chapter_feed = {extension['new_feed']!r}\n"
        f"    chapter_feed_profile = {extension['chapter_feed_profile']!r}\n"
        f"    popular_is_latest = {extension['popular_is_latest']!r}\n"
        f"    popular_profile = {extension['popular_profile']!r}\n"
        f"    request_referer = {extension['request_referer']!r}\n"
        f"    search_profile = {extension['search_profile']!r}\n"
        f"    chapter_profile = {extension['chapter_profile']!r}\n"
        f"    chapter_categories = {extension['chapter_categories']!r}\n"
        f"    use_old_chapter_feed = {extension['old_feed']!r}\n"
        f"    pages_profile = {extension['pages_profile']!r}\n"
        f"    latest_order = {extension['latest_order']!r}\n"
        f"    strip_series_query = {extension['strip_series_query']!r}\n"
        f"    supports_latest = {extension['supports_latest']!r}\n"
        f"    requests_per_minute = {extension['requests_per_minute']!r}\n"
        f"    has_filters = {extension['has_filters']!r}\n"
        f"    has_language_filter = {extension['has_language_filter']!r}\n"
        f"    excluded_categories = {extension['excluded_categories']!r}\n"
        f"    status_filters = {extension['status_filters']!r}\n"
        f"    type_filters = {extension['type_filters']!r}\n"
        f"    genre_filters = {extension['genre_filters']!r}\n"
        f"    details_profile = {extension['details_profile']!r}\n"
        f"    content_warning = {extension['content_warning']!r}\n"
        f"    paginate_chapter_feed = {extension['paginate_chapter_feed']!r}\n"
        "\n\nSOURCE = GeneratedZeistMangaSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _guya_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedGuyaSource(GuyaSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        "\n\nSOURCE = GeneratedGuyaSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _grouple_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedGroupLeSource(GroupLeSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        "\n\nSOURCE = GeneratedGroupLeSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _manga18_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedManga18Source(Manga18Source):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        "\n\nSOURCE = GeneratedManga18Source\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _manhwaz_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedManhwaZSource(ManhwaZSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        f"    search_path = {extension['search_path']!r}\n"
        f"    popular_catalog_path = {extension['popular_catalog_path']!r}\n"
        "\n\nSOURCE = GeneratedManhwaZSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _madtheme_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedMadThemeSource(MadThemeSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        f"    use_legacy_api = {extension['legacy_api']!r}\n"
        f"    use_slug_search = {extension['slug_search']!r}\n"
        "\n\nSOURCE = GeneratedMadThemeSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _natsuid_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedNatsuIdSource(NatsuIdSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        f"    chapter_page = {extension['chapter_page']!r}\n"
        "\n\nSOURCE = GeneratedNatsuIdSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _liliana_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedLilianaSource(LilianaSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        f"    profile = {extension['profile']!r}\n"
        "\n\nSOURCE = GeneratedLilianaSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _mangareader_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedMangaReaderSource(MangaReaderSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        f"    search_at_root = {extension['search_at_root']!r}\n"
        f"    search_keyword = {extension['search_keyword']!r}\n"
        f"    page_parameter = {extension['page_parameter']!r}\n"
        f"    chapter_container_id = {extension['chapter_container_id']!r}\n"
        f"    ajax_kind = {extension['ajax_kind']!r}\n"
        f"    exclude_manganow_placeholder = {extension['exclude_placeholder']!r}\n"
        "\n\nSOURCE = GeneratedMangaReaderSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _uzaymanga_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedUzayMangaSource(UzayMangaSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        f"    cdn_url = {extension['cdn_url']!r}\n"
        "\n\nSOURCE = GeneratedUzayMangaSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _colorlibanime_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedColorlibAnimeSource(ColorlibAnimeSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        "\n\nSOURCE = GeneratedColorlibAnimeSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _bakkin_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedBakkinSource(BakkinSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        "\n\nSOURCE = GeneratedBakkinSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _mangaworld_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedMangaWorldSource(MangaWorldSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        "\n\nSOURCE = GeneratedMangaWorldSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _oceanwp_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedOceanWPSource(OceanWPSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        "\n\nSOURCE = GeneratedOceanWPSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _monochrome_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedMonochromeSource(MonochromeSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    api_url = {extension['api_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        "\n\nSOURCE = GeneratedMonochromeSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _multichan_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedMultiChanSource(MultiChanSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        f"    profile = {extension['profile']!r}\n"
        "\n\nSOURCE = GeneratedMultiChanSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _goda_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedGodaSource(GodaSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        f"    profile = {extension['profile']!r}\n"
        "\n\nSOURCE = GeneratedGodaSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _gattsu_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedGattsuSource(GattsuSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        f"    profile = {extension['profile']!r}\n"
        "\n\nSOURCE = GeneratedGattsuSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _moonlighttl_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedMoonlightTLSource(MoonlightTLSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        f"    profile = {extension['profile']!r}\n"
        f"    content_warning = {extension['content_warning']!r}\n"
        "\n\nSOURCE = GeneratedMoonlightTLSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _scanreader_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedScanReaderSource(ScanReaderSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        "\n\nSOURCE = GeneratedScanReaderSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _heancms_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedHeanCmsSource(HeanCmsSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        f"    new_query = {extension['new_query']!r}\n"
        f"    latest_order = {extension['latest_order']!r}\n"
        "\n\nSOURCE = GeneratedHeanCmsSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _fuzzydoodle_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedFuzzyDoodleSource(FuzzyDoodleSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        f"    latest_profile = {extension['latest_profile']!r}\n"
        "\n\nSOURCE = GeneratedFuzzyDoodleSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _spicytheme_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedSpicyThemeSource(SpicyThemeSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        f"    api_base_url = {extension['api_base_url']!r}\n"
        "\n\nSOURCE = GeneratedSpicyThemeSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _mangadventure_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedMangAdventureSource(MangAdventureSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        "\n\nSOURCE = GeneratedMangAdventureSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _mangawork_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedMangaWorkSource(MangaWorkSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        f"    series_path = {extension['series_path']!r}\n"
        "\n\nSOURCE = GeneratedMangaWorkSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _ezmanhwa_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedEZManhwaSource(EZManhwaSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    api_url = {extension['api_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        "\n\nSOURCE = GeneratedEZManhwaSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _fansubscat_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedFansubsCatSource(FansubsCatSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        "\n\nSOURCE = GeneratedFansubsCatSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _kemono_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedKemonoSource(KemonoSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        "\n\nSOURCE = GeneratedKemonoSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _mangataro_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedMangaTaroSource(MangaTaroSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        "\n\nSOURCE = GeneratedMangaTaroSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _mangabox_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedMangaBoxSource(MangaBoxSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        f"    chapter_profile = {extension['chapter_profile']!r}\n"
        "\n\nSOURCE = GeneratedMangaBoxSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _fmreader_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedFMReaderSource(FMReaderSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        f"    profile = {extension['profile']!r}\n"
        "\n\nSOURCE = GeneratedFMReaderSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _stalkercms_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedStalkerCmsSource(StalkerCmsSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        "\n\nSOURCE = GeneratedStalkerCmsSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _vercomics_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedVerComicsSource(VerComicsSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        f"    url_suffix = {extension['url_suffix']!r}\n"
        f"    use_suffix_on_search = {extension['use_suffix_on_search']!r}\n"
        "\n\nSOURCE = GeneratedVerComicsSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _senkuro_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedSenkuroSource(SenkuroSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        "\n\nSOURCE = GeneratedSenkuroSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _hiper_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedHiperSource(HiperSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        f"    extra_headers = {extension['headers']!r}\n"
        "\n\nSOURCE = GeneratedHiperSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _greenshit_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedGreenShitSource(GreenShitSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        f"    api_url = {extension['api_url']!r}\n"
        f"    cdn_url = {extension['cdn_url']!r}\n"
        f"    scan_id = {extension['scan_id']!r}\n"
        f"    default_genre_id = {extension['default_genre_id']!r}\n"
        "\n\nSOURCE = GeneratedGreenShitSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _libgroup_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedLibGroupSource(LibGroupSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        f"    site_id = {extension['site_id']!r}\n"
        "\n\nSOURCE = GeneratedLibGroupSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _mccms_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedMCCMSSource(MCCMSSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        "\n\nSOURCE = GeneratedMCCMSSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _zmanga_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedZMangaSource(ZMangaSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        "\n\nSOURCE = GeneratedZMangaSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _hentaihand_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedHentaiHandSource(HentaiHandSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        f"    language_ids = {extension['language_ids']!r}\n"
        "\n\nSOURCE = GeneratedHentaiHandSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _eromuse_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedEroMuseSource(EroMuseSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        f"    profile = {extension['profile']!r}\n"
        "\n\nSOURCE = GeneratedEroMuseSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _galleryadults_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedGalleryAdultsSource(GalleryAdultsSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        f"    manga_language = {extension['manga_language']!r}\n"
        f"    profile = {extension['profile']!r}\n"
        "\n\nSOURCE = GeneratedGalleryAdultsSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def _mangahub_bundle(
    common_engine: str,
    theme_engine: str,
    extension: dict[str, object],
) -> bytes:
    config = (
        "\n\nclass GeneratedMangaHubSource(MangaHubSource):\n"
        f"    name = {extension['id']!r}\n"
        f"    display_name = {extension['name']!r}\n"
        f"    base_url = {extension['base_url']!r}\n"
        f"    language = {extension['language']!r}\n"
        f"    manga_source = {extension['manga_source']!r}\n"
        "\n\nSOURCE = GeneratedMangaHubSource\n"
    )
    return (common_engine.rstrip() + "\n\n" + theme_engine.rstrip() + config).encode()


def generate(repo: Path, source_root: Path, base_url: str) -> tuple[dict[str, int], dict[str, int]]:
    v4_engine = (repo / "engines" / "v4.py").read_bytes()

    def finalize(bundle: bytes) -> bytes:
        return bundle.rstrip() + b"\n\n" + v4_engine.rstrip() + b"\n\nSOURCE = adapt_source(SOURCE)\n"

    madara_engine = (repo / "engines" / "madara.py").read_text(encoding="utf-8")
    mangathemesia_engine = (repo / "engines" / "mangathemesia.py").read_text(encoding="utf-8")
    pizzareader_engine = (repo / "engines" / "pizzareader.py").read_text(encoding="utf-8")
    mangacatalog_engine = (repo / "engines" / "mangacatalog.py").read_text(encoding="utf-8")
    masonry_engine = (repo / "engines" / "masonry.py").read_text(encoding="utf-8")
    iken_engine = (repo / "engines" / "iken.py").read_text(encoding="utf-8")
    keyoapp_engine = (repo / "engines" / "keyoapp.py").read_text(encoding="utf-8")
    foolslide_engine = (repo / "engines" / "foolslide.py").read_text(encoding="utf-8")
    comiciviewer_engine = (repo / "engines" / "comiciviewer.py").read_text(encoding="utf-8")
    wpcomics_engine = (repo / "engines" / "wpcomics.py").read_text(encoding="utf-8")
    gigaviewer_engine = (repo / "engines" / "gigaviewer.py").read_text(encoding="utf-8")
    generic_engine = (repo / "engines" / "generic.py").read_text(encoding="utf-8")
    heavenmanga_engine = (repo / "engines" / "heavenmanga.py").read_text(encoding="utf-8")
    hentaihall_engine = (repo / "engines" / "hentaihall.py").read_text(encoding="utf-8")
    mangadex_engine = (repo / "engines" / "mangadex.py").read_text(encoding="utf-8")
    zeistmanga_engine = (repo / "engines" / "zeistmanga.py").read_text(encoding="utf-8")
    guya_engine = (repo / "engines" / "guya.py").read_text(encoding="utf-8")
    grouple_engine = (repo / "engines" / "grouple.py").read_text(encoding="utf-8")
    manga18_engine = (repo / "engines" / "manga18.py").read_text(encoding="utf-8")
    manhwaz_engine = (repo / "engines" / "manhwaz.py").read_text(encoding="utf-8")
    madtheme_engine = (repo / "engines" / "madtheme.py").read_text(encoding="utf-8")
    natsuid_engine = (repo / "engines" / "natsuid.py").read_text(encoding="utf-8")
    liliana_engine = (repo / "engines" / "liliana.py").read_text(encoding="utf-8")
    mangareader_engine = (repo / "engines" / "mangareader.py").read_text(encoding="utf-8")
    uzaymanga_engine = (repo / "engines" / "uzaymanga.py").read_text(encoding="utf-8")
    colorlibanime_engine = (repo / "engines" / "colorlibanime.py").read_text(encoding="utf-8")
    bakkin_engine = (repo / "engines" / "bakkin.py").read_text(encoding="utf-8")
    mangaworld_engine = (repo / "engines" / "mangaworld.py").read_text(encoding="utf-8")
    oceanwp_engine = (repo / "engines" / "oceanwp.py").read_text(encoding="utf-8")
    monochrome_engine = (repo / "engines" / "monochrome.py").read_text(encoding="utf-8")
    multichan_engine = (repo / "engines" / "multichan.py").read_text(encoding="utf-8")
    goda_engine = (repo / "engines" / "goda.py").read_text(encoding="utf-8")
    gattsu_engine = (repo / "engines" / "gattsu.py").read_text(encoding="utf-8")
    moonlighttl_engine = (repo / "engines" / "moonlighttl.py").read_text(encoding="utf-8")
    scanreader_engine = (repo / "engines" / "scanreader.py").read_text(encoding="utf-8")
    heancms_engine = (repo / "engines" / "heancms.py").read_text(encoding="utf-8")
    fuzzydoodle_engine = (repo / "engines" / "fuzzydoodle.py").read_text(encoding="utf-8")
    spicytheme_engine = (repo / "engines" / "spicytheme.py").read_text(encoding="utf-8")
    mangadventure_engine = (repo / "engines" / "mangadventure.py").read_text(encoding="utf-8")
    mangawork_engine = (repo / "engines" / "mangawork.py").read_text(encoding="utf-8")
    ezmanhwa_engine = (repo / "engines" / "ezmanhwa.py").read_text(encoding="utf-8")
    fansubscat_engine = (repo / "engines" / "fansubscat.py").read_text(encoding="utf-8")
    kemono_engine = (repo / "engines" / "kemono.py").read_text(encoding="utf-8")
    mangataro_engine = (repo / "engines" / "mangataro.py").read_text(encoding="utf-8")
    mangabox_engine = (repo / "engines" / "mangabox.py").read_text(encoding="utf-8")
    fmreader_engine = (repo / "engines" / "fmreader.py").read_text(encoding="utf-8")
    stalkercms_engine = (repo / "engines" / "stalkercms.py").read_text(encoding="utf-8")
    vercomics_engine = (repo / "engines" / "vercomics.py").read_text(encoding="utf-8")
    senkuro_engine = (repo / "engines" / "senkuro.py").read_text(encoding="utf-8")
    hiper_engine = (repo / "engines" / "hiper.py").read_text(encoding="utf-8")
    greenshit_engine = (repo / "engines" / "greenshit.py").read_text(encoding="utf-8")
    libgroup_engine = (repo / "engines" / "libgroup.py").read_text(encoding="utf-8")
    mccms_engine = (repo / "engines" / "mccms.py").read_text(encoding="utf-8")
    zmanga_engine = (repo / "engines" / "zmanga.py").read_text(encoding="utf-8")
    hentaihand_engine = (repo / "engines" / "hentaihand.py").read_text(encoding="utf-8")
    eromuse_engine = (repo / "engines" / "eromuse.py").read_text(encoding="utf-8")
    galleryadults_engine = (repo / "engines" / "galleryadults.py").read_text(encoding="utf-8")
    mangahub_engine = (repo / "engines" / "mangahub.py").read_text(encoding="utf-8")
    bundles_dir = repo / "bundles"
    icons_dir = repo / "icons"
    bundles_dir.mkdir(exist_ok=True)
    icons_dir.mkdir(exist_ok=True)

    index_path = repo / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    generated_engines = {
        "bakkin",
        "colorlibanime",
        "comiciviewer",
        "wpcomics",
        "gigaviewer",
        "madara",
        "mangadex",
        "iken",
        "foolslide",
        "grouple",
        "goda",
        "gattsu",
        "moonlighttl",
        "scanreader",
        "heancms",
        "fuzzydoodle",
        "spicytheme",
        "mangadventure",
        "mangawork",
        "ezmanhwa",
        "fansubscat",
        "kemono",
        "mangataro",
        "mangabox",
        "fmreader",
        "stalkercms",
        "vercomics",
        "senkuro",
        "hiper",
        "greenshit",
        "libgroup",
        "mccms",
        "zmanga",
        "hentaihand",
        "eromuse",
        "galleryadults",
        "mangahub",
        "guya",
        "keyoapp",
        "liliana",
        "madtheme",
        "manga18",
        "mangacatalog",
        "mangareader",
        "mangaworld",
        "manhwaz",
        "mangathemesia",
        "masonry",
        "monochrome",
        "multichan",
        "natsuid",
        "oceanwp",
        "pizzareader",
        "uzaymanga",
        "zeistmanga",
    }
    specialized_engines = set(generated_engines)
    generated_engines.update(
        _match(r'theme\s*=\s*"([^"]+)"', path.read_text(encoding="utf-8")) or "custom"
        for path in source_root.glob("*/*/build.gradle.kts")
    )
    manual = [item for item in index["extensions"] if item.get("engine") not in generated_engines]
    generated: list[dict[str, object]] = []
    counts = {engine: 0 for engine in generated_engines}
    skipped = {engine: 0 for engine in generated_engines}

    for build_path in sorted(source_root.glob("*/*/build.gradle.kts")):
        build = build_path.read_text(encoding="utf-8")
        engine_name = _match(r'theme\s*=\s*"([^"]+)"', build) or (
            "mangadex" if build_path.parent.name == "mangadex" else "custom"
        )
        if engine_name == "mangadex":
            languages = re.findall(r'"([^"]+)"', _match(r"listOf\(([\s\S]*?)\)\.forEach", build))
            icon = _source_icon(build_path, source_root, engine_name)
            for language in languages:
                extension = _supported_mangadex(build_path.parent, build, language)
                extension_id = str(extension["id"])
                extension["content_warning"] = _extract_kotlin_metadata(build_path.parent)
                bundle_bytes = _mangadex_bundle(mangadex_engine, extension)
                manual_path = repo / "engines" / "manual" / f"{extension_id}.py"
                if manual_path.exists():
                    bundle_bytes = _manual_bundle(manual_path, madara_engine)
                bundle_bytes = finalize(bundle_bytes)
                (bundles_dir / f"{extension_id}.py").write_bytes(bundle_bytes)
                shutil.copyfile(icon, icons_dir / f"{extension_id}.png")
                generated.append(
                    {
                        "id": extension_id,
                        "name": extension["name"],
                        "version": extension["version"],
                        "sha256": hashlib.sha256(bundle_bytes).hexdigest(),
                        "bundle_url": f"{base_url}/bundles/{extension_id}.py",
                        "icon_url": f"{base_url}/icons/{extension_id}.png",
                        "author": "Keiyoushi contributors",
                        "engine": engine_name,
                    }
                )
                counts[engine_name] += 1
            continue
        if engine_name not in specialized_engines:
            source_blocks = _source_blocks(build) or [""]
            languages = re.findall(r'"([^"]+)"', _match(r"listOf\(([\s\S]*?)\)\.forEach", build))
            variants = [
                variant
                for source in source_blocks
                for variant in (
                    [
                        _expand_source(source, language)
                        for language in languages
                    ]
                    if re.search(r"\blang\s*=\s*it\b", source) and languages
                    else [source]
                )
            ]
            icon = _source_icon(build_path, source_root, engine_name)
            for source in variants:
                is_heavenmanga = build_path.parent.name == "heavenmanga"
                is_hentaihall = build_path.parent.name == "hentaihall"
                is_ikigaimangas = build_path.parent.name == "ikigaimangas"
                is_ikuhentai = build_path.parent.name == "ikuhentai"
                is_inmanga = build_path.parent.name == "inmanga"
                is_insanosscan = build_path.parent.name == "insanosscan"
                is_koinoboriscan = build_path.parent.name == "koinoboriscan"
                is_leercapitulo = build_path.parent.name == "leercapitulo"
                is_leermangaesp = build_path.parent.name == "leermangaesp"
                is_lectorjpg = build_path.parent.name == "lectorjpg"
                is_lmtoonline = build_path.parent.name == "lmtoonline"
                extension = (
                    _supported_heavenmanga(build_path.parent, build)
                    if is_heavenmanga
                    else _supported_hentaihall(build_path.parent, build)
                    if is_hentaihall
                    else _supported_generic(build_path.parent, build, source)
                )
                if extension is None:
                    skipped[engine_name] += 1
                    continue
                extension_id = str(extension["id"])
                used_ids = {str(item["id"]) for item in generated}
                if extension_id in used_ids:
                    extension_id = f"{extension_id}_{_slug(str(extension['name']))}"
                    suffix = 2
                    while extension_id in used_ids:
                        extension_id = f"{extension['id']}_{suffix}"
                        suffix += 1
                    extension["id"] = extension_id
                extension["content_warning"] = _extract_kotlin_metadata(build_path.parent)
                bundle_bytes = (
                    _heavenmanga_bundle(madara_engine, heavenmanga_engine, extension)
                    if is_heavenmanga
                    else _hentaihall_bundle(madara_engine, hentaihall_engine, extension)
                    if is_hentaihall
                    else _generic_bundle(madara_engine, generic_engine, extension)
                )
                manual_path = repo / "engines" / "manual" / f"{extension_id}.py"
                if manual_path.exists() and not (is_heavenmanga or is_hentaihall or is_ikigaimangas or is_ikuhentai or is_inmanga or is_insanosscan or is_koinoboriscan or is_leercapitulo or is_leermangaesp or is_lectorjpg or is_lmtoonline or build_path.parent.name == "mangamx"):
                    bundle_bytes = _manual_bundle(manual_path, madara_engine)
                bundle_bytes = finalize(bundle_bytes)
                (bundles_dir / f"{extension_id}.py").write_bytes(bundle_bytes)
                shutil.copyfile(icon, icons_dir / f"{extension_id}.png")
                generated.append(
                    {
                        "id": extension_id,
                        "name": extension["name"],
                        "version": extension["version"],
                        "sha256": hashlib.sha256(bundle_bytes).hexdigest(),
                        "bundle_url": f"{base_url}/bundles/{extension_id}.py",
                        "icon_url": f"{base_url}/icons/{extension_id}.png",
                        "author": "Keiyoushi contributors",
                        "engine": engine_name,
                    }
                )
                counts[engine_name] += 1
            continue
        if engine_name == "madara":
            source_blocks = _source_blocks(build)
            variants = source_blocks
            if len(source_blocks) == 1 and re.search(r"\blang\s*=\s*it\b", source_blocks[0]):
                languages = re.findall(r'"([^"]+)"', _match(r"listOf\(([^)]+)\)", build))
                variants = [
                    _expand_source(source_blocks[0], language)
                    for language in languages
                ]
            if len(variants) != 1 or variants != source_blocks:
                icon = _source_icon(build_path, source_root, engine_name)
                for source in variants:
                    extension = _supported_madara(build_path.parent, build, source)
                    if extension is None:
                        skipped[engine_name] += 1
                        continue
                    extension_id = str(extension["id"])
                    extension["content_warning"] = _extract_kotlin_metadata(build_path.parent)
                    bundle_bytes = _madara_bundle(madara_engine, extension)
                    manual_path = repo / "engines" / "manual" / f"{extension_id}.py"
                    if manual_path.exists():
                        bundle_bytes = _manual_bundle(manual_path, madara_engine)
                    bundle_bytes = finalize(bundle_bytes)
                    (bundles_dir / f"{extension_id}.py").write_bytes(bundle_bytes)
                    shutil.copyfile(icon, icons_dir / f"{extension_id}.png")
                    generated.append(
                        {
                            "id": extension_id,
                            "name": extension["name"],
                            "version": extension["version"],
                            "sha256": hashlib.sha256(bundle_bytes).hexdigest(),
                            "bundle_url": f"{base_url}/bundles/{extension_id}.py",
                            "icon_url": f"{base_url}/icons/{extension_id}.png",
                            "author": "Keiyoushi contributors",
                            "engine": engine_name,
                        }
                    )
                    counts[engine_name] += 1
                continue
        if engine_name == "mangathemesia" and build_path.parent.name == "miauscan":
            languages = re.findall(r'"([^"]+)"', _match(r"listOf\(([^)]+)\)", build))
            icon = _source_icon(build_path, source_root, engine_name)
            for language in languages:
                extension = _supported_mangathemesia(build_path.parent, build, language)
                if extension is None:
                    continue
                extension_id = str(extension["id"])
                bundle_bytes = _mangathemesia_bundle(
                    madara_engine,
                    mangathemesia_engine,
                    extension,
                )
                manual_path = repo / "engines" / "manual" / f"{extension_id}.py"
                if manual_path.exists():
                    bundle_bytes = _manual_bundle(manual_path, madara_engine)
                bundle_bytes = finalize(bundle_bytes)
                (bundles_dir / f"{extension_id}.py").write_bytes(bundle_bytes)
                shutil.copyfile(icon, icons_dir / f"{extension_id}.png")
                generated.append(
                    {
                        "id": extension_id,
                        "name": extension["name"],
                        "version": extension["version"],
                        "sha256": hashlib.sha256(bundle_bytes).hexdigest(),
                        "bundle_url": f"{base_url}/bundles/{extension_id}.py",
                        "icon_url": f"{base_url}/icons/{extension_id}.png",
                        "author": "Keiyoushi contributors",
                        "engine": engine_name,
                    }
                )
                counts[engine_name] += 1
            continue
        if engine_name == "galleryadults":
            extensions = _supported_galleryadults(build_path.parent, build)
            if not extensions:
                skipped[engine_name] += 1
                continue
            icon = _source_icon(build_path, source_root, engine_name)
            for extension in extensions:
                extension_id = str(extension["id"])
                extension["content_warning"] = _extract_kotlin_metadata(build_path.parent)
                bundle_bytes = _galleryadults_bundle(madara_engine, galleryadults_engine, extension)
                manual_path = repo / "engines" / "manual" / f"{extension_id}.py"
                if manual_path.exists():
                    bundle_bytes = _manual_bundle(manual_path, madara_engine)
                bundle_bytes = finalize(bundle_bytes)
                (bundles_dir / f"{extension_id}.py").write_bytes(bundle_bytes)
                shutil.copyfile(icon, icons_dir / f"{extension_id}.png")
                generated.append(
                    {
                        "id": extension_id,
                        "name": extension["name"],
                        "version": extension["version"],
                        "sha256": hashlib.sha256(bundle_bytes).hexdigest(),
                        "bundle_url": f"{base_url}/bundles/{extension_id}.py",
                        "icon_url": f"{base_url}/icons/{extension_id}.png",
                        "author": "Keiyoushi contributors",
                        "engine": engine_name,
                    }
                )
                counts[engine_name] += 1
            continue
        if engine_name == "hentaihand":
            extensions = _supported_hentaihand(build_path.parent, build)
            if not extensions:
                skipped[engine_name] += 1
                continue
            icon = _source_icon(build_path, source_root, engine_name)
            for extension in extensions:
                extension_id = str(extension["id"])
                extension["content_warning"] = _extract_kotlin_metadata(build_path.parent)
                bundle_bytes = _hentaihand_bundle(madara_engine, hentaihand_engine, extension)
                manual_path = repo / "engines" / "manual" / f"{extension_id}.py"
                if manual_path.exists():
                    bundle_bytes = _manual_bundle(manual_path, madara_engine)
                bundle_bytes = finalize(bundle_bytes)
                (bundles_dir / f"{extension_id}.py").write_bytes(bundle_bytes)
                shutil.copyfile(icon, icons_dir / f"{extension_id}.png")
                generated.append(
                    {
                        "id": extension_id,
                        "name": extension["name"],
                        "version": extension["version"],
                        "sha256": hashlib.sha256(bundle_bytes).hexdigest(),
                        "bundle_url": f"{base_url}/bundles/{extension_id}.py",
                        "icon_url": f"{base_url}/icons/{extension_id}.png",
                        "author": "Keiyoushi contributors",
                        "engine": engine_name,
                    }
                )
                counts[engine_name] += 1
            continue
        if engine_name == "vercomics":
            extensions = _supported_vercomics(build_path.parent, build)
            if not extensions:
                skipped[engine_name] += 1
                continue
            icon = _source_icon(build_path, source_root, engine_name)
            for extension in extensions:
                extension_id = str(extension["id"])
                bundle_bytes = _vercomics_bundle(
                    madara_engine,
                    vercomics_engine,
                    extension,
                )
                manual_path = repo / "engines" / "manual" / f"{extension_id}.py"
                if manual_path.exists():
                    bundle_bytes = _manual_bundle(manual_path, madara_engine)
                bundle_bytes = finalize(bundle_bytes)
                (bundles_dir / f"{extension_id}.py").write_bytes(bundle_bytes)
                shutil.copyfile(icon, icons_dir / f"{extension_id}.png")
                generated.append(
                    {
                        "id": extension_id,
                        "name": extension["name"],
                        "version": extension["version"],
                        "sha256": hashlib.sha256(bundle_bytes).hexdigest(),
                        "bundle_url": f"{base_url}/bundles/{extension_id}.py",
                        "icon_url": f"{base_url}/icons/{extension_id}.png",
                        "author": "Keiyoushi contributors",
                        "engine": engine_name,
                    }
                )
                counts[engine_name] += 1
            continue
        if engine_name == "madara":
            extension = _supported_madara(build_path.parent, build)
        elif engine_name == "mangathemesia":
            extension = _supported_mangathemesia(build_path.parent, build)
        elif engine_name == "pizzareader":
            extension = _supported_pizzareader(build_path.parent, build)
        elif engine_name == "mangacatalog":
            extension = _supported_mangacatalog(build_path.parent, build)
        elif engine_name == "masonry":
            extension = _supported_masonry(build_path.parent, build)
        elif engine_name == "iken":
            extension = _supported_iken(build_path.parent, build)
        elif engine_name == "keyoapp":
            extension = _supported_keyoapp(build_path.parent, build)
        elif engine_name == "foolslide":
            extension = _supported_foolslide(build_path.parent, build)
        elif engine_name == "comiciviewer":
            extension = _supported_comiciviewer(build_path.parent, build)
        elif engine_name == "wpcomics":
            extension = _supported_wpcomics(build_path.parent, build)
        elif engine_name == "gigaviewer":
            extension = _supported_gigaviewer(build_path.parent, build)
        elif engine_name == "zeistmanga":
            extension = _supported_zeistmanga(build_path.parent, build)
        elif engine_name == "guya":
            extension = _supported_guya(build_path.parent, build)
        elif engine_name == "grouple":
            extension = _supported_grouple(build_path.parent, build)
        elif engine_name == "manga18":
            extension = _supported_manga18(build_path.parent, build)
        elif engine_name == "manhwaz":
            extension = _supported_manhwaz(build_path.parent, build)
        elif engine_name == "madtheme":
            extension = _supported_madtheme(build_path.parent, build)
        elif engine_name == "natsuid":
            extension = _supported_natsuid(build_path.parent, build)
        elif engine_name == "liliana":
            extension = _supported_liliana(build_path.parent, build)
        elif engine_name == "mangareader":
            extension = _supported_mangareader(build_path.parent, build)
        elif engine_name == "uzaymanga":
            extension = _supported_uzaymanga(build_path.parent, build)
        elif engine_name == "colorlibanime":
            extension = _supported_colorlibanime(build_path.parent, build)
        elif engine_name == "bakkin":
            extension = _supported_bakkin(build_path.parent, build)
        elif engine_name == "mangaworld":
            extension = _supported_mangaworld(build_path.parent, build)
        elif engine_name == "oceanwp":
            extension = _supported_oceanwp(build_path.parent, build)
        elif engine_name == "monochrome":
            extension = _supported_monochrome(build_path.parent, build)
        elif engine_name == "multichan":
            extension = _supported_multichan(build_path.parent, build)
        elif engine_name == "goda":
            extension = _supported_goda(build_path.parent, build)
        elif engine_name == "gattsu":
            extension = _supported_gattsu(build_path.parent, build)
        elif engine_name == "moonlighttl":
            extension = _supported_moonlighttl(build_path.parent, build)
        elif engine_name == "scanreader":
            extension = _supported_scanreader(build_path.parent, build)
        elif engine_name == "heancms":
            extension = _supported_heancms(build_path.parent, build)
        elif engine_name == "fuzzydoodle":
            extension = _supported_fuzzydoodle(build_path.parent, build)
        elif engine_name == "spicytheme":
            extension = _supported_spicytheme(build_path.parent, build)
        elif engine_name == "mangadventure":
            extension = _supported_mangadventure(build_path.parent, build)
        elif engine_name == "mangawork":
            extension = _supported_mangawork(build_path.parent, build)
        elif engine_name == "ezmanhwa":
            extension = _supported_ezmanhwa(build_path.parent, build)
        elif engine_name == "fansubscat":
            extension = _supported_fansubscat(build_path.parent, build)
        elif engine_name == "kemono":
            extension = _supported_kemono(build_path.parent, build)
        elif engine_name == "mangataro":
            extension = _supported_mangataro(build_path.parent, build)
        elif engine_name == "mangabox":
            extension = _supported_mangabox(build_path.parent, build)
        elif engine_name == "fmreader":
            extension = _supported_fmreader(build_path.parent, build)
        elif engine_name == "stalkercms":
            extension = _supported_stalkercms(build_path.parent, build)
        elif engine_name == "senkuro":
            extension = _supported_senkuro(build_path.parent, build)
        elif engine_name == "hiper":
            extension = _supported_hiper(build_path.parent, build)
        elif engine_name == "greenshit":
            extension = _supported_greenshit(build_path.parent, build)
        elif engine_name == "libgroup":
            extension = _supported_libgroup(build_path.parent, build)
        elif engine_name == "mccms":
            extension = _supported_mccms(build_path.parent, build)
        elif engine_name == "zmanga":
            extension = _supported_zmanga(build_path.parent, build)
        elif engine_name == "eromuse":
            extension = _supported_eromuse(build_path.parent, build)
        elif engine_name == "mangahub":
            extension = _supported_mangahub(build_path.parent, build)
        else:
            continue
        if extension is None:
            skipped[engine_name] += 1
            continue

        extension_id = str(extension["id"])
        if engine_name == "madara":
            extension["content_warning"] = _extract_kotlin_metadata(build_path.parent)
            bundle_bytes = _madara_bundle(madara_engine, extension)
        elif engine_name == "mangathemesia":
            bundle_bytes = _mangathemesia_bundle(
                madara_engine,
                mangathemesia_engine,
                extension,
            )
        elif engine_name == "pizzareader":
            bundle_bytes = _pizzareader_bundle(
                madara_engine,
                pizzareader_engine,
                extension,
            )
        elif engine_name == "mangacatalog":
            bundle_bytes = _mangacatalog_bundle(
                madara_engine,
                mangacatalog_engine,
                extension,
            )
        elif engine_name == "masonry":
            bundle_bytes = _masonry_bundle(
                madara_engine,
                masonry_engine,
                extension,
            )
        elif engine_name == "iken":
            bundle_bytes = _iken_bundle(
                madara_engine,
                iken_engine,
                extension,
            )
        elif engine_name == "keyoapp":
            bundle_bytes = _keyoapp_bundle(
                madara_engine,
                keyoapp_engine,
                extension,
            )
        elif engine_name == "foolslide":
            bundle_bytes = _foolslide_bundle(
                madara_engine,
                foolslide_engine,
                extension,
            )
        elif engine_name == "comiciviewer":
            bundle_bytes = _comiciviewer_bundle(
                madara_engine,
                comiciviewer_engine,
                extension,
            )
        elif engine_name == "wpcomics":
            bundle_bytes = _wpcomics_bundle(
                madara_engine,
                wpcomics_engine,
                extension,
            )
        elif engine_name == "gigaviewer":
            bundle_bytes = _gigaviewer_bundle(
                madara_engine,
                gigaviewer_engine,
                extension,
            )
        elif engine_name == "zeistmanga":
            bundle_bytes = _zeistmanga_bundle(
                madara_engine,
                zeistmanga_engine,
                extension,
            )
        elif engine_name == "guya":
            bundle_bytes = _guya_bundle(
                madara_engine,
                guya_engine,
                extension,
            )
        elif engine_name == "grouple":
            bundle_bytes = _grouple_bundle(
                madara_engine,
                grouple_engine,
                extension,
            )
        elif engine_name == "manga18":
            bundle_bytes = _manga18_bundle(
                madara_engine,
                manga18_engine,
                extension,
            )
        elif engine_name == "manhwaz":
            bundle_bytes = _manhwaz_bundle(
                madara_engine,
                manhwaz_engine,
                extension,
            )
        elif engine_name == "madtheme":
            bundle_bytes = _madtheme_bundle(
                madara_engine,
                madtheme_engine,
                extension,
            )
        elif engine_name == "natsuid":
            bundle_bytes = _natsuid_bundle(
                madara_engine,
                natsuid_engine,
                extension,
            )
        elif engine_name == "liliana":
            bundle_bytes = _liliana_bundle(
                madara_engine,
                liliana_engine,
                extension,
            )
        elif engine_name == "mangareader":
            bundle_bytes = _mangareader_bundle(
                madara_engine,
                mangareader_engine,
                extension,
            )
        elif engine_name == "uzaymanga":
            bundle_bytes = _uzaymanga_bundle(
                madara_engine,
                uzaymanga_engine,
                extension,
            )
        elif engine_name == "colorlibanime":
            bundle_bytes = _colorlibanime_bundle(
                madara_engine,
                colorlibanime_engine,
                extension,
            )
        elif engine_name == "bakkin":
            bundle_bytes = _bakkin_bundle(
                madara_engine,
                bakkin_engine,
                extension,
            )
        elif engine_name == "mangaworld":
            bundle_bytes = _mangaworld_bundle(
                madara_engine,
                mangaworld_engine,
                extension,
            )
        elif engine_name == "oceanwp":
            bundle_bytes = _oceanwp_bundle(
                madara_engine,
                oceanwp_engine,
                extension,
            )
        elif engine_name == "monochrome":
            bundle_bytes = _monochrome_bundle(
                madara_engine,
                monochrome_engine,
                extension,
            )
        elif engine_name == "multichan":
            bundle_bytes = _multichan_bundle(
                madara_engine,
                multichan_engine,
                extension,
            )
        elif engine_name == "goda":
            bundle_bytes = _goda_bundle(
                madara_engine,
                goda_engine,
                extension,
            )
        elif engine_name == "gattsu":
            bundle_bytes = _gattsu_bundle(
                madara_engine,
                gattsu_engine,
                extension,
            )
        elif engine_name == "moonlighttl":
            bundle_bytes = _moonlighttl_bundle(
                madara_engine,
                moonlighttl_engine,
                extension,
            )
        elif engine_name == "scanreader":
            bundle_bytes = _scanreader_bundle(
                madara_engine,
                scanreader_engine,
                extension,
            )
        elif engine_name == "heancms":
            bundle_bytes = _heancms_bundle(
                madara_engine,
                heancms_engine,
                extension,
            )
        elif engine_name == "fuzzydoodle":
            bundle_bytes = _fuzzydoodle_bundle(
                madara_engine,
                fuzzydoodle_engine,
                extension,
            )
        elif engine_name == "spicytheme":
            bundle_bytes = _spicytheme_bundle(
                madara_engine,
                spicytheme_engine,
                extension,
            )
        elif engine_name == "mangadventure":
            bundle_bytes = _mangadventure_bundle(
                madara_engine,
                mangadventure_engine,
                extension,
            )
        elif engine_name == "mangawork":
            bundle_bytes = _mangawork_bundle(
                madara_engine,
                mangawork_engine,
                extension,
            )
        elif engine_name == "ezmanhwa":
            bundle_bytes = _ezmanhwa_bundle(
                madara_engine,
                ezmanhwa_engine,
                extension,
            )
        elif engine_name == "fansubscat":
            bundle_bytes = _fansubscat_bundle(
                madara_engine,
                fansubscat_engine,
                extension,
            )
        elif engine_name == "kemono":
            bundle_bytes = _kemono_bundle(
                madara_engine,
                kemono_engine,
                extension,
            )
        elif engine_name == "mangataro":
            bundle_bytes = _mangataro_bundle(
                madara_engine,
                mangataro_engine,
                extension,
            )
        elif engine_name == "mangabox":
            bundle_bytes = _mangabox_bundle(
                madara_engine,
                mangabox_engine,
                extension,
            )
        elif engine_name == "fmreader":
            bundle_bytes = _fmreader_bundle(
                madara_engine,
                fmreader_engine,
                extension,
            )
        elif engine_name == "stalkercms":
            bundle_bytes = _stalkercms_bundle(
                madara_engine,
                stalkercms_engine,
                extension,
            )
        elif engine_name == "senkuro":
            bundle_bytes = _senkuro_bundle(
                madara_engine,
                senkuro_engine,
                extension,
            )
        elif engine_name == "hiper":
            bundle_bytes = _hiper_bundle(
                madara_engine,
                hiper_engine,
                extension,
            )
        elif engine_name == "greenshit":
            bundle_bytes = _greenshit_bundle(
                madara_engine,
                greenshit_engine,
                extension,
            )
        elif engine_name == "libgroup":
            bundle_bytes = _libgroup_bundle(
                madara_engine,
                libgroup_engine,
                extension,
            )
        elif engine_name == "mccms":
            bundle_bytes = _mccms_bundle(
                madara_engine,
                mccms_engine,
                extension,
            )
        elif engine_name == "zmanga":
            bundle_bytes = _zmanga_bundle(
                madara_engine,
                zmanga_engine,
                extension,
            )
        elif engine_name == "eromuse":
            bundle_bytes = _eromuse_bundle(
                madara_engine,
                eromuse_engine,
                extension,
            )
        else:
            bundle_bytes = _mangahub_bundle(
                madara_engine,
                mangahub_engine,
                extension,
            )
        manual_path = repo / "engines" / "manual" / f"{extension_id}.py"
        if manual_path.exists() and build_path.parent.name not in {"lectormangalat", "mangacrab", "mangaesp", "mangatv"}:
            bundle_bytes = _manual_bundle(manual_path, madara_engine)
        bundle_bytes = finalize(bundle_bytes)
        (bundles_dir / f"{extension_id}.py").write_bytes(bundle_bytes)

        icon = _source_icon(build_path, source_root, engine_name)
        shutil.copyfile(icon, icons_dir / f"{extension_id}.png")

        generated.append(
            {
                "id": extension_id,
                "name": extension["name"],
                "version": extension["version"],
                "sha256": hashlib.sha256(bundle_bytes).hexdigest(),
                "bundle_url": f"{base_url}/bundles/{extension_id}.py",
                "icon_url": f"{base_url}/icons/{extension_id}.png",
                "author": "Keiyoushi contributors",
                "engine": engine_name,
            }
        )
        counts[engine_name] += 1

    languages: dict[str, str] = {}
    for item in generated:
        bundle = (bundles_dir / f"{item['id']}.py").read_text(encoding="utf-8")
        matches = re.findall(r"""^\s+language\s*=\s*['"]([^'"]+)['"]""", bundle, re.M)
        if matches:
            item["language"] = languages[str(item["id"])] = matches[-1]
    _disambiguate_names(generated, languages)

    by_id = {item["id"]: item for item in manual}
    by_id.update((item["id"], item) for item in generated)
    index["extensions"] = sorted(by_id.values(), key=lambda item: item["id"])
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return counts, skipped


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-root",
        type=Path,
        default=repo.parent / "extensions-source-main" / "src",
    )
    # El default es el repo publico, NO el servidor local. `index.json` se commitea, y con
    # una URL local la app no descarga nada fuera de esta maquina. Ya paso dos veces por
    # olvidar el flag al regenerar; para desarrollo se pasa --base-url explicitamente.
    parser.add_argument(
        "--base-url",
        default="https://raw.githubusercontent.com/kfernandoy/nyanko-extensions/main",
    )
    args = parser.parse_args()
    generated, skipped = generate(repo, args.source_root.resolve(), args.base_url.rstrip("/"))
    for engine in sorted(generated):
        print(
            f"{engine}: generadas {generated[engine]}; "
            f"pendientes por overrides {skipped[engine]}"
        )


if __name__ == "__main__":
    main()
