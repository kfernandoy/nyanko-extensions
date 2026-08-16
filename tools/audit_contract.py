"""Audita las fuentes manuales y los bundles contra el contrato Source del backend.

El backend rechaza un bundle con 422 ("La fuente no cumple el contrato Source")
cuando `isinstance(instancia, Source)` da False. `Source` es un Protocol
runtime-checkable, asi que solo comprueba la PRESENCIA de estos miembros:

    name, display_name, api_version, capabilities,
    search, browse, chapters, pages, page_bytes

Uso:
    python tools/audit_contract.py manual     # audita engines/manual/*.py
    python tools/audit_contract.py bundles    # audita bundles/*.py
    python tools/audit_contract.py            # ambos
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Miembros que el Protocol `Source` exige (ver nyanko_api/sources/contract.py).
REQUIRED = (
    "name",
    "display_name",
    "api_version",
    "capabilities",
    "search",
    "browse",
    "chapters",
    "pages",
    "page_bytes",
)

# Clases que aporta un motor inyectado; no son la fuente concreta del bundle.
ENGINE_CLASSES = {
    "FuenteBaseSource",
    "MadaraSource",
    "MadaraDetailsSource",
    "MangaThemesiaSource",
    "GenericSource",
    "WPComicsSource",
}


def _classes(tree: ast.Module) -> dict[str, ast.ClassDef]:
    return {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}


def _source_target(tree: ast.Module) -> str | None:
    """Devuelve el nombre al que apunta `SOURCE = ...` o `build_source`."""
    target = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "SOURCE":
                    if isinstance(node.value, ast.Name):
                        target = node.value.id
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == "build_source":
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Call):
                        fn = sub.value.func
                        if isinstance(fn, ast.Name):
                            target = fn.id
    return target


def _members(name: str, classes: dict[str, ast.ClassDef], seen: set[str]) -> set[str]:
    """Miembros de `name` incluyendo los heredados dentro del mismo archivo."""
    if name in seen or name not in classes:
        return set()
    seen.add(name)
    node = classes[name]
    found: set[str] = set()
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.add(item.name)
        elif isinstance(item, ast.Assign):
            for t in item.targets:
                if isinstance(t, ast.Name):
                    found.add(t.id)
        elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            found.add(item.target.id)
    for base in node.bases:
        if isinstance(base, ast.Name):
            found |= _members(base.id, classes, seen)
    return found


def audit(directory: Path) -> int:
    files = sorted(directory.glob("*.py"))
    if not files:
        print(f"  (sin archivos en {directory})")
        return 0

    broken: list[tuple[str, list[str]]] = []
    dupes: list[tuple[str, str, list[str]]] = []
    no_target: list[str] = []
    unparsable: list[tuple[str, str]] = []

    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            unparsable.append((path.name, f"linea {exc.lineno}: {exc.msg}"))
            continue

        classes = _classes(tree)
        target = _source_target(tree)
        if target is None:
            no_target.append(path.name)
            continue

        missing = [m for m in REQUIRED if m not in _members(target, classes, set())]
        if missing:
            broken.append((path.name, missing))

        # Fuentes concretas rivales: sintoma del wrapper Generated duplicado.
        concretas = [n for n in classes if n not in ENGINE_CLASSES]
        rivales = [
            n
            for n in concretas
            if n != target and not _members(n, classes, set()).isdisjoint({"pages", "chapters"})
        ]
        if rivales:
            dupes.append((path.name, target, rivales))

    print(f"  archivos analizados : {len(files)}")
    print(f"  sin SOURCE/build    : {len(no_target)}")
    print(f"  no parsean          : {len(unparsable)}")
    print(f"  INCUMPLEN contrato  : {len(broken)}")
    print(f"  clases duplicadas   : {len(dupes)}")

    for name, err in unparsable[:10]:
        print(f"    [syntax] {name}: {err}")
    for name in no_target[:10]:
        print(f"    [sin SOURCE] {name}")
    for name, target, rivales in dupes[:15]:
        print(f"    [duplicada] {name}: SOURCE={target} rivales={rivales}")
    for name, missing in broken[:25]:
        print(f"    [incompleta] {name}: falta {missing}")
    if len(broken) > 25:
        print(f"    ... y {len(broken) - 25} mas")

    return len(broken) + len(unparsable) + len(no_target)


def main() -> int:
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    total = 0
    if which in {"manual", "all"}:
        print("== engines/manual ==")
        total += audit(REPO / "engines" / "manual")
    if which in {"bundles", "all"}:
        print("== bundles ==")
        total += audit(REPO / "bundles")
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
