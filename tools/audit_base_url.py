"""Detecta bundles cuyo base_url quedo vacio o corrupto.

Sintoma encontrado en manta_es: `base_url = ''` y `api_url = '://'`. Una fuente
asi no puede pedir nada, y falla en tiempo de ejecucion aunque cumpla el
contrato (que solo comprueba que los atributos existan).
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BACKEND = Path(r"E:\2023-09-04\anitracker\Nyanko\apps\backend")


def instanciar(ext_id: str):
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    path = REPO / "bundles" / f"{ext_id}.py"
    if not path.exists():
        return None, "sin bundle"
    mod = types.ModuleType(ext_id)
    mod.__dict__["__file__"] = str(path)
    try:
        exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), mod.__dict__)
    except Exception as exc:  # noqa: BLE001
        return None, f"EXEC {type(exc).__name__}"
    src = mod.__dict__.get("SOURCE")
    if src is None:
        return None, "sin SOURCE"
    try:
        return src(), None
    except Exception as exc:  # noqa: BLE001
        return None, f"INIT {type(exc).__name__}"


def main(ids: list[str]) -> int:
    if not ids:
        data = json.loads((REPO / "index.json").read_text(encoding="utf-8"))
        ids = [e["id"] for e in data["extensions"]]

    vacios: list[str] = []
    corruptos: list[tuple[str, str]] = []
    rotos: list[tuple[str, str]] = []
    bien = 0

    for ext_id in ids:
        source, error = instanciar(ext_id)
        if source is None:
            rotos.append((ext_id, error or "?"))
            continue
        base = getattr(source, "base_url", None)
        if base is None:
            continue
        if not isinstance(base, str) or not base.strip():
            vacios.append(ext_id)
        elif not base.startswith("http") or base.strip() in {"://", "https://", "http://"}:
            corruptos.append((ext_id, base))
        else:
            bien += 1

    print(f"revisadas          : {len(ids)}")
    print(f"base_url correcto  : {bien}")
    print(f"base_url VACIO     : {len(vacios)}")
    print(f"base_url CORRUPTO  : {len(corruptos)}")
    print(f"bundle no cargable : {len(rotos)}")
    if vacios:
        print(f"\nVACIOS: {vacios[:40]}")
    if corruptos:
        print(f"\nCORRUPTOS: {corruptos[:20]}")
    if rotos:
        print(f"\nNO CARGABLES: {rotos[:20]}")

    (REPO / ".audit_base_url.json").write_text(
        json.dumps(
            {"vacios": vacios, "corruptos": corruptos, "rotos": rotos},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
