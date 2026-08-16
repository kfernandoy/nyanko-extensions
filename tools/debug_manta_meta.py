"""Reproduce la extraccion de metadatos de generate.py para el modulo Manta."""

from __future__ import annotations

import re
from pathlib import Path

GRADLE = Path(
    r"E:\2023-09-04\anitracker\extensions-source-main\src\all\manta\build.gradle.kts"
)


def source_block(text: str) -> str:
    found = re.search(r"\bsource\s*\{", text)
    if found is None:
        return ""
    depth = 1
    index = found.end()
    while index < len(text) and depth:
        depth += (text[index] == "{") - (text[index] == "}")
        index += 1
    return text[found.end() : index - 1]


def main() -> int:
    texto = GRADLE.read_text(encoding="utf-8")
    print("--- build.gradle.kts ---")
    print(texto.strip()[:500])

    bloque = source_block(texto)
    print("\n--- bloque source { } extraido ---")
    print(bloque.strip() or "(vacio)")

    actual = re.findall(r'baseUrl\s*=\s*"(https?://[^"]+)"', bloque)
    print(f"\nregex ACTUAL (exige literal http)   -> {actual}")

    laxo = re.findall(r'baseUrl\s*=\s*"([^"]+)"', bloque)
    print(f"regex LAXO (acepta interpolacion)   -> {laxo}")

    todo = re.findall(r'baseUrl\s*=\s*"([^"]+)"', texto)
    print(f"en todo el fichero                  -> {todo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
