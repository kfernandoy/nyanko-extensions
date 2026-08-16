"""Comprueba si `_refrescar_motor_en_manual` logra inyectar el motor.

Para cada manual indicado imprime la clase raiz que el manual espera (la que
importa del motor) y las clases que el motor realmente define. Si no coinciden,
el refresco no encuentra donde cortar, devuelve el manual sin tocar y el bundle
sale con el stub `class X: pass` en lugar del motor.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MANUAL = REPO / "engines" / "manual"
ENGINES = REPO / "engines"

IMPORT_RE = re.compile(r"from \.(\w+) import \(?\s*([\w, \n]+)", re.M)


def engine_classes(name: str) -> list[str]:
    path = ENGINES / f"{name}.py"
    if not path.exists():
        return []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    return [n.name for n in tree.body if isinstance(n, ast.ClassDef)]


def main(names: list[str]) -> int:
    for name in names:
        path = MANUAL / f"{name}.py"
        if not path.exists():
            print(f"== {name}: SIN MANUAL")
            continue
        text = path.read_text(encoding="utf-8")
        print(f"== {name}  ({len(text)}B)")

        match = IMPORT_RE.search(text)
        if not match:
            print("   sin import de motor")
            continue
        module = match.group(1)
        imported = [n.strip() for n in match.group(2).replace("\n", " ").split(",") if n.strip()]
        print(f"   importa de .{module}: {imported}")

        stubs = re.findall(r"^class (\w+)\s*:\s*\n\s*pass", text, re.M)
        print(f"   stubs vacios en el manual: {stubs}")

        defined = engine_classes(module)
        print(f"   engines/{module}.py define: {defined[:8]}")

        root = imported[0] if imported else None
        if root and defined:
            print(f"   raiz '{root}' presente en el motor: {root in defined}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
