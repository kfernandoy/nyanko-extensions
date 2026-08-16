"""Comprueba que index.json esta en sintonia con los bundles del disco.

Es la precondicion para que instalar/actualizar funcione: la app descarga el
bundle y valida su sha256 contra el indice. Si no cuadran, la instalacion falla
aunque el bundle sea correcto.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def main() -> int:
    data = json.loads((REPO / "index.json").read_text(encoding="utf-8"))
    extensions = data["extensions"]

    faltan: list[str] = []
    descuadran: list[str] = []
    sin_hash: list[str] = []

    for entry in extensions:
        ext_id = entry["id"]
        bundle = REPO / "bundles" / f"{ext_id}.py"
        if not bundle.exists():
            faltan.append(ext_id)
            continue
        esperado = entry.get("sha256") or entry.get("hash") or ""
        if not esperado:
            sin_hash.append(ext_id)
            continue
        real = hashlib.sha256(bundle.read_bytes()).hexdigest()
        if real != esperado:
            descuadran.append(ext_id)

    print(f"extensiones en el indice : {len(extensions)}")
    print(f"bundles ausentes         : {len(faltan)}")
    print(f"sin sha256 en el indice  : {len(sin_hash)}")
    print(f"sha256 que NO cuadran    : {len(descuadran)}")

    for etiqueta, grupo in (
        ("AUSENTES", faltan),
        ("SIN HASH", sin_hash),
        ("DESCUADRAN", descuadran),
    ):
        if grupo:
            print(f"\n{etiqueta}: {grupo[:10]}")

    huerfanos = sorted(
        p.stem
        for p in (REPO / "bundles").glob("*.py")
        if p.stem != "__init__" and p.stem not in {e["id"] for e in extensions}
    )
    print(f"\nbundles fuera del indice : {len(huerfanos)} {huerfanos[:10]}")

    return 0 if not (faltan or descuadran) else 1


if __name__ == "__main__":
    raise SystemExit(main())
