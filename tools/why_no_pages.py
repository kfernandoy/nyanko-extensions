"""Diagnostica por que a una fuente le falta el metodo `pages` del contrato.

Muestra, para cada manual indicado, sus clases con la base y los metodos
definidos, y si `pages` aparece en algun punto de la cadena.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MANUAL = REPO / "engines" / "manual"


def bases(cls: ast.ClassDef) -> str:
    out = []
    for b in cls.bases:
        if isinstance(b, ast.Name):
            out.append(b.id)
        elif isinstance(b, ast.Attribute):
            out.append(b.attr)
        else:
            out.append("?")
    return ", ".join(out)


def methods(cls: ast.ClassDef) -> list[str]:
    return [
        f.name
        for f in cls.body
        if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def main(names: list[str]) -> int:
    for name in names:
        path = MANUAL / f"{name}.py"
        if not path.exists():
            print(f"== {name}: SIN MANUAL")
            continue
        text = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            print(f"== {name}: SYNTAX {exc.lineno} {exc.msg}")
            continue

        print(f"== {name}")
        for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
            ms = methods(cls)
            mark = "  <-- tiene pages" if "pages" in ms else ""
            print(f"   {cls.name}({bases(cls)}){mark}")
            print(f"      {ms}")
        srcs = [l.strip() for l in text.splitlines() if l.startswith("SOURCE")]
        print(f"   {srcs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
