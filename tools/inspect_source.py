"""Inspecciona la instancia real de un bundle contra el contrato Source.

Muestra que miembro del Protocol falta y con que valor, que es lo que decide
entre "La fuente no cumple el contrato Source" y "Version de API incompatible".

Uso:
    python tools/inspect_source.py anzmanga_es akuma_es doujinshell_es
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BACKEND = REPO.parent / "Nyanko" / "apps" / "backend"

sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(REPO / "tools"))

from verify_bundles import _exec_bundle  # noqa: E402

REQUIRED = (
    "name", "display_name", "api_version", "capabilities",
    "search", "browse", "chapters", "pages", "page_bytes",
)


def main() -> int:
    try:
        from nyanko_api.sources.contract import SOURCE_API_VERSION, Source  # type: ignore
    except Exception as exc:
        print(f"no se pudo importar el contrato: {exc}")
        return 1

    for stem in sys.argv[1:]:
        path = REPO / "bundles" / f"{stem}.py"
        print(f"\n=== {stem} ===")
        if not path.exists():
            print("  no existe")
            continue
        try:
            module = _exec_bundle(path)
        except Exception as exc:
            print(f"  no ejecuta: {type(exc).__name__}: {exc}")
            continue

        factory = getattr(module, "SOURCE", None) or getattr(module, "build_source", None)
        print(f"  factory        : {factory!r}")
        if factory is None:
            continue
        try:
            inst = factory()
        except Exception as exc:
            print(f"  no instancia   : {type(exc).__name__}: {exc}")
            continue

        print(f"  tipo           : {type(inst).__name__}")
        print(f"  MRO            : {[c.__name__ for c in type(inst).__mro__[:6]]}")
        print(f"  isinstance     : {isinstance(inst, Source)}")
        print(f"  app api_version: {SOURCE_API_VERSION}")
        for member in REQUIRED:
            if not hasattr(inst, member):
                print(f"    FALTA  {member}")
            else:
                value = getattr(inst, member)
                if member in {"name", "display_name", "api_version"}:
                    print(f"    ok     {member} = {value!r}")
                elif member == "capabilities":
                    print(f"    ok     {member} = {type(value).__name__}")
                else:
                    print(f"    ok     {member} (callable={callable(value)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
