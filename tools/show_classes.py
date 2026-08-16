"""Lista las clases y su herencia en bundles o manuales. Diagnostico rapido de MRO."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def base_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return "?"


def main() -> int:
    where = "bundles"
    args = sys.argv[1:]
    if args and args[0] in {"bundles", "manual"}:
        where = args.pop(0)
    directory = REPO / ("bundles" if where == "bundles" else "engines/manual")

    for stem in args:
        path = directory / f"{stem}.py"
        if not path.exists():
            print(f"--- {stem}: NO EXISTE en {where}")
            continue
        src = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src)
        except SyntaxError as exc:
            print(f"--- {stem}: SYNTAX linea {exc.lineno}: {exc.msg}")
            continue
        classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
        names = [c.name for c in classes]
        print(f"--- {stem}  ({len(src)} bytes, {len(classes)} clases)")
        print(f"    FuenteBaseSource presente: {'FuenteBaseSource' in names}")
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            print(f"    NOMBRES DUPLICADOS: {sorted(dupes)}")
        for c in classes:
            bases = ", ".join(base_name(b) for b in c.bases)
            print(f"      {c.lineno:>6} class {c.name}({bases})")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
