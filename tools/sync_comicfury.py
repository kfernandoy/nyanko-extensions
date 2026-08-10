"""Propaga la logica de ComicFury del manual `_es` al resto de variantes de idioma.

Solo `comicfury_es.py` llevaba la clase `ComicFurySource`; las otras 13 se quedaron en el
`GeneratedGenericSource` heuristico, que no sabe leer el sitio (0 portadas, ficha vacia,
paginas sin resolver y las mismas 85 series en todos los idiomas).

Cada variante es el mismo archivo con tres cosas cambiadas: `name`, `language` y el codigo
de idioma que se le manda a `search.php`. Este script las regenera desde el `_es` para que
no haya que mantener 14 copias a mano.

Uso: python tools/sync_comicfury.py [--check]
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

MANUAL = pathlib.Path(__file__).resolve().parent.parent / "engines" / "manual"
ORIGEN = MANUAL / "comicfury_es.py"

# id de extension -> (language de la extension, codigo que espera search.php)
# El sitio ofrece: en es pt de fr it pl ja zh ru fi notext other, y "" para All.
VARIANTES: dict[str, tuple[str, str]] = {
    "comicfury_all": ("all", ""),
    "comicfury_de": ("de", "de"),
    "comicfury_en": ("en", "en"),
    "comicfury_es": ("es", "es"),
    "comicfury_fi": ("fi", "fi"),
    "comicfury_fr": ("fr", "fr"),
    "comicfury_it": ("it", "it"),
    "comicfury_ja": ("ja", "ja"),
    "comicfury_other": ("other", "other"),
    # El "No Text" del sitio es un idioma mas (`notext`), no una variante aparte.
    "comicfury_other_comic_fury_no_text": ("other", "notext"),
    "comicfury_pl": ("pl", "pl"),
    # ComicFury no distingue pt-BR de pt: su codigo es "pt".
    "comicfury_pt_br": ("pt-BR", "pt"),
    "comicfury_ru": ("ru", "ru"),
    "comicfury_zh": ("zh", "zh"),
}

NOMBRES = {"comicfury_other_comic_fury_no_text": "Comic Fury (No Text)"}


def render(plantilla: str, extension_id: str) -> str:
    idioma, codigo = VARIANTES[extension_id]
    salida = plantilla
    salida = re.sub(r"(?m)^(    name = )'comicfury_es'", rf"\g<1>'{extension_id}'", salida)
    salida = re.sub(r"(?m)^(    language = )'es'", rf"\g<1>'{idioma}'", salida)
    salida = re.sub(
        r"(?m)^(    search_language = )\"es\"", rf'\g<1>"{codigo}"', salida,
    )
    nombre = NOMBRES.get(extension_id)
    if nombre:
        salida = re.sub(r"(?m)^(    display_name = )'Comic Fury'", rf"\g<1>'{nombre}'", salida)
    return salida


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="solo comprobar, no escribir")
    args = parser.parse_args()

    plantilla = ORIGEN.read_text(encoding="utf-8")
    if "search_language" not in plantilla:
        print("El manual `_es` no declara `search_language`; abortando.")
        return 1

    desincronizados: list[str] = []
    for extension_id in VARIANTES:
        if extension_id == "comicfury_es":
            continue
        destino = MANUAL / f"{extension_id}.py"
        esperado = render(plantilla, extension_id)
        if destino.exists() and destino.read_text(encoding="utf-8") == esperado:
            continue
        desincronizados.append(extension_id)
        if not args.check:
            destino.write_text(esperado, encoding="utf-8")

    if args.check:
        print("\n".join(desincronizados) or "todas las variantes al dia")
        return 1 if desincronizados else 0
    print(f"actualizadas {len(desincronizados)} variantes: {', '.join(desincronizados) or 'ninguna'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
