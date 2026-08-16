"""Alinea comicfury_es con sus 13 hermanos de idioma.

comicfury_es era el unico de las 14 variantes que importaba de `.base` con un
stub `FuenteBaseSource: pass` en lugar de heredar la cadena real
MadaraSource -> GenericSource -> GeneratedGenericSource -> ComicFurySource.
Su SOURCE apuntaba al stub, que no define `pages`, asi que la fuente no cumplia
el contrato Source v4.

Se reconstruye a partir de comicfury_en (identico salvo el idioma) sustituyendo
solo los valores de idioma.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MANUAL = REPO / "engines" / "manual"

REPLACEMENTS = (
    ("'comicfury_en'", "'comicfury_es'"),
    ('"comicfury_en"', '"comicfury_es"'),
    ("language = 'en'", "language = 'es'"),
    ('language = "en"', 'language = "es"'),
    ("search_language = 'en'", "search_language = 'es'"),
    ('search_language = "en"', 'search_language = "es"'),
)


def main() -> int:
    src = (MANUAL / "comicfury_en.py").read_text(encoding="utf-8")
    out = src
    for old, new in REPLACEMENTS:
        out = out.replace(old, new)

    if "comicfury_en" in out:
        leftovers = [l for l in out.splitlines() if "comicfury_en" in l]
        print("AVISO: quedan referencias a comicfury_en:")
        for line in leftovers:
            print("   ", line.strip())
        return 1

    (MANUAL / "comicfury_es.py").write_text(out, encoding="utf-8")
    print(f"comicfury_es.py reescrito desde comicfury_en ({len(out)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
