"""Valida bundles igual que la app: ejecuta el fichero e instancia SOURCE.

Sirve tanto para bundles locales como para los descargados del RAW de GitHub,
de modo que la comparacion sea exactamente la misma prueba en ambos lados.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

BACKEND = r"E:\2023-09-04\anitracker\Nyanko\apps\backend"


def contract_attrs() -> list[str]:
    sys.path.insert(0, BACKEND)
    module = __import__("nyanko_api.sources.contract", fromlist=["Source"])
    return sorted(getattr(module.Source, "__protocol_attrs__"))


def check(path: Path, required: list[str]) -> str | None:
    mod = types.ModuleType(path.stem)
    mod.__dict__["__file__"] = str(path)
    try:
        exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), mod.__dict__)
    except Exception as exc:  # noqa: BLE001
        return f"EXEC {type(exc).__name__}: {str(exc)[:70]}"
    source = mod.__dict__.get("SOURCE")
    if source is None:
        return "SIN SOURCE"
    try:
        instance = source()
    except Exception as exc:  # noqa: BLE001
        return f"INIT {type(exc).__name__}: {str(exc)[:70]}"
    missing = [a for a in required if not hasattr(instance, a)]
    return f"FALTA {missing}" if missing else None


def main(argv: list[str]) -> int:
    required = contract_attrs()
    directory = Path(argv[0]) if argv else Path("bundles")
    names = argv[1:]

    paths = (
        [directory / f"{n}.py" for n in names]
        if names
        else sorted(p for p in directory.glob("*.py") if p.stem != "__init__")
    )

    ok = 0
    bad: list[tuple[str, str]] = []
    for path in paths:
        if not path.exists():
            bad.append((path.stem, "NO EXISTE"))
            continue
        problem = check(path, required)
        if problem:
            bad.append((path.stem, problem))
        else:
            ok += 1

    print(f"=== {directory} ===")
    print(f"OK    : {ok}")
    print(f"FALLAN: {len(bad)}")
    for name, problem in bad:
        print(f"   {name} -> {problem}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
