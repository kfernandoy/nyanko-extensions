"""Clasifica por que 36 bundles quedaron sin base_url.

Hipotesis a confirmar: keiyoushi migro los metadatos al bloque `keiyoushi { }`
de `build.gradle.kts`, a menudo con interpolacion (`baseUrl = "https://x/$it"`),
mientras que generate.py los busca por regex en los `.kt`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
KOTLIN = Path(r"E:\2023-09-04\anitracker\extensions-source-main")

BLOQUE_KEIYOUSHI = re.compile(r"keiyoushi\s*\{", re.S)
BASEURL_GRADLE = re.compile(r'baseUrl\s*=\s*"([^"]+)"')
BASEURL_KT = re.compile(r'(?:override\s+)?val\s+baseUrl\s*(?::\s*String\s*)?=\s*"([^"]+)"')


def buscar_modulo(ext_id: str) -> Path | None:
    """Localiza la carpeta del modulo Kotlin a partir del id de la extension."""
    raiz = ext_id.rsplit("_", 1)[0].replace("_", "")
    src = KOTLIN / "src"
    if not src.exists():
        return None
    for lang_dir in src.iterdir():
        if not lang_dir.is_dir():
            continue
        for modulo in lang_dir.iterdir():
            if not modulo.is_dir():
                continue
            if modulo.name.replace("-", "").replace("_", "").lower() == raiz.lower():
                return modulo
    return None


def main() -> int:
    datos = json.loads((REPO / ".audit_base_url.json").read_text(encoding="utf-8"))
    vacios = datos["vacios"]

    clasificacion: dict[str, list[str]] = {
        "gradle_interpolado": [],
        "gradle_literal": [],
        "kt_literal": [],
        "sin_baseurl": [],
        "modulo_no_encontrado": [],
    }

    for ext_id in vacios:
        modulo = buscar_modulo(ext_id)
        if modulo is None:
            clasificacion["modulo_no_encontrado"].append(ext_id)
            continue

        gradle = modulo / "build.gradle.kts"
        texto_gradle = gradle.read_text(encoding="utf-8", errors="replace") if gradle.exists() else ""
        valores_gradle = BASEURL_GRADLE.findall(texto_gradle)

        valores_kt: list[str] = []
        for kt in modulo.rglob("*.kt"):
            valores_kt += BASEURL_KT.findall(kt.read_text(encoding="utf-8", errors="replace"))

        if valores_gradle:
            interpolado = any("$" in v for v in valores_gradle)
            clave = "gradle_interpolado" if interpolado else "gradle_literal"
            clasificacion[clave].append(f"{ext_id} -> {valores_gradle[0]}")
        elif valores_kt:
            clasificacion["kt_literal"].append(f"{ext_id} -> {valores_kt[0]}")
        else:
            clasificacion["sin_baseurl"].append(ext_id)

    for clave, grupo in clasificacion.items():
        print(f"\n=== {clave}: {len(grupo)}")
        for item in grupo[:12]:
            print(f"   {item}")

    (REPO / ".audit_kotlin_meta.json").write_text(
        json.dumps(clasificacion, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
