"""Resume un smoke.json en una linea por extension, para leerlo de un vistazo.

Uso: python tools/resumen_smoke.py [fichero.json]
"""

from __future__ import annotations

import json
import pathlib
import sys

CAMPOS = ["popular", "latest", "search", "details", "chapters", "pages", "page_bytes"]


def celda(nombre: str, paso: dict | None) -> str:
    if not paso:
        return f"{nombre}=-"
    if paso.get("status") == "error":
        return f"{nombre}=ERR({paso.get('error', '')[:70]})"
    if "items" in paso:
        cobertura = paso.get("cover")
        return f"{nombre}={paso['items']}" + (f"/{cobertura}" if cobertura is not None else "")
    if "bytes" in paso:
        return f"{nombre}={paso['bytes']}"
    if nombre == "details":
        # `details` reporta un 1/0 por campo del contrato, no un conteo de items.
        presentes = [k for k in ("cover_url", "description", "author", "artist", "status", "content_tags") if paso.get(k)]
        return f"details={'+'.join(presentes) if presentes else 'vacio'}"
    return f"{nombre}={paso.get('status')}"


def main() -> None:
    ruta = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "smoke.json")
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    for registro in sorted(datos, key=lambda x: x["id"]):
        pasos = registro.get("steps", {})
        linea = " ".join(celda(nombre, pasos.get(nombre)) for nombre in CAMPOS)
        extras = {k: v for k, v in pasos.items() if k not in CAMPOS}
        if extras:
            linea += " " + " ".join(celda(k, v) for k, v in extras.items())
        print(f"{registro['id']:<24} {registro.get('engine', ''):<14} {linea}")


if __name__ == "__main__":
    main()
