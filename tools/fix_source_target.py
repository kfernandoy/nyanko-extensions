"""Apunta SOURCE al overlay que implementa el contrato, no al stub base.

Tras restaurar los manuales recortados por la purga, varios quedaron con dos
clases: la propia del manual, que hereda de `FuenteBaseSource` y NO trae
`pages`, y el overlay `GeneratedGenericSource(GenericSource)`, que si lo trae
por heredar la cadena Madara completa.

`SOURCE` seguia apuntando a la primera, asi que la fuente no cumplia el
contrato Source v4. En los manuales que ya pasaban (comikey_es, ikuhentai_es)
el overlay reutiliza el nombre de la clase del manual, y por eso `SOURCE` si lo
capturaba: aqui se replica ese mismo resultado moviendo la asignacion.

Solo actua cuando el destino actual de SOURCE carece de `pages` y existe otra
clase del modulo que si lo implementa.
"""

from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MANUAL = REPO / "engines" / "manual"

CONTRACT_METHOD = "pages"


def methods(cls: ast.ClassDef) -> set[str]:
    return {
        f.name
        for f in cls.body
        if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    names = args.names or [p.stem for p in sorted(MANUAL.glob("*.py"))]
    changed = 0

    for name in names:
        path = MANUAL / f"{name}.py"
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue

        match = re.search(r"^SOURCE = (\w+)\s*$", text, re.M)
        if not match:
            continue
        current = match.group(1)

        classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
        by_name = {c.name: c for c in classes}
        target = by_name.get(current)
        if target is None:
            continue

        # Solo interesa el caso roto: el destino de SOURCE no alcanza `pages`
        # por ninguna via, ni propia ni heredada dentro del propio modulo.
        def reaches_pages(cls: ast.ClassDef, seen: set[str] | None = None) -> bool:
            seen = seen or set()
            if cls.name in seen:
                return False
            seen.add(cls.name)
            if CONTRACT_METHOD in methods(cls):
                return True
            for base in cls.bases:
                if isinstance(base, ast.Name) and base.id in by_name:
                    if reaches_pages(by_name[base.id], seen):
                        return True
            return False

        if reaches_pages(target):
            continue

        candidates = [c for c in classes if reaches_pages(c)]
        if not candidates:
            continue
        winner = candidates[-1].name

        print(f"{name}: SOURCE {current} -> {winner}")
        if args.apply:
            new = text[: match.start()] + f"SOURCE = {winner}" + text[match.end() :]
            path.write_text(new, encoding="utf-8")
            changed += 1

    if args.apply:
        print(f"\nactualizados: {changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
