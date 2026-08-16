"""Detecta manuales cuyo `SOURCE = X` apunta a una clase que no existe.

Es el dano que dejo la purga de wrappers `Generated*Source`: borro la clase
concreta pero conservo la asignacion `SOURCE`, produciendo un NameError al
ejecutar el bundle (y por tanto el 422 en la instalacion).

Uso:
    python tools/find_missing_source.py            # informe
    python tools/find_missing_source.py --recover  # busca la clase en el historial
"""

from __future__ import annotations

import argparse
import ast
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MANUAL = REPO / "engines" / "manual"

# Commits sanos anteriores a la purga `eedde66`, del mas reciente al mas antiguo.
FALLBACK_COMMITS = ("2e0fdcb", "0b54c67", "bcb2a33", "35ddce7")


def source_target(tree: ast.Module) -> str | None:
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "SOURCE":
                    if isinstance(node.value, ast.Name):
                        return node.value.id
    return None


def defined_names(tree: ast.Module) -> set[str]:
    names = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
    return names


def git_show(commit: str, path: str) -> str | None:
    res = subprocess.run(
        ["git", "show", f"{commit}:{path}"],
        capture_output=True,
        cwd=REPO,
    )
    if res.returncode != 0:
        return None
    return res.stdout.decode("utf-8", errors="replace")


def find_class_source(commit: str, rel: str, cls: str) -> str | None:
    """Extrae el bloque textual de `class cls` en ese commit (sin usar ast,
    porque las copias antiguas tienen errores de sintaxis conocidos)."""
    text = git_show(commit, rel)
    if text is None:
        return None
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.startswith(f"class {cls}(") or line.startswith(f"class {cls}:"):
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        line = lines[j]
        if line and not line[0].isspace() and not line.startswith(")"):
            end = j
            break
    return "\n".join(lines[start:end]).rstrip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--recover", action="store_true")
    args = ap.parse_args()

    broken: list[tuple[Path, str]] = []
    for path in sorted(MANUAL.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        target = source_target(tree)
        if target and target not in defined_names(tree):
            broken.append((path, target))

    print(f"manuales con SOURCE colgando: {len(broken)}\n")
    for path, target in broken:
        rel = f"engines/manual/{path.name}"
        note = ""
        if args.recover:
            for commit in FALLBACK_COMMITS:
                block = find_class_source(commit, rel, target)
                if block:
                    note = f"  <- recuperable de {commit} ({len(block)} bytes)"
                    break
            else:
                note = "  <- NO ENCONTRADA en el historial"
        print(f"  {path.name}: SOURCE={target}{note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
