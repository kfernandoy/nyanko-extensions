"""Regenera en memoria el bundle de unas pocas fuentes y lo valida.

Evita regenerar los 1916 bundles para probar un cambio en generate.py.
Escribe el resultado en .tmp_regen/ para poder inspeccionarlo.

Uso:
    python tools/try_regen.py comicskingdom_es doujinshell_es anzmanga_es
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO))

import generate  # noqa: E402


def build_engine(name: str) -> str:
    base = (REPO / "engines" / "base.py").read_text(encoding="utf-8")
    details = (REPO / "engines" / "madara_details.py").read_text(encoding="utf-8")
    madara = (REPO / "engines" / "madara.py").read_text(encoding="utf-8")
    generic = (REPO / "engines" / "generic.py").read_text(encoding="utf-8")
    detalles = base.rstrip() + "\n\n" + details
    if name == "madara":
        return detalles.rstrip() + "\n\n" + madara
    if name == "generic":
        return detalles.rstrip() + "\n\n" + madara.rstrip() + "\n\n" + generic
    return detalles


def finalize(bundle: bytes, v4_engine: bytes) -> bytes:
    """Replica generate.finalize(): normaliza __future__ y engancha adapt_source."""
    bundle = bundle.replace(b"from __future__ import annotations\n", b"").replace(
        b"from __future__ import annotations\r\n", b""
    )
    bundle = bundle.replace(b"from __future__ import annotations", b"")
    return (
        b"from __future__ import annotations\n\n"
        + bundle.rstrip()
        + b"\n\n"
        + v4_engine.rstrip()
        + b"\n\nSOURCE = adapt_source(SOURCE)\n"
    )


def main() -> int:
    out = REPO / ".tmp_regen"
    out.mkdir(exist_ok=True)
    engine = build_engine("madara")
    v4_engine = (REPO / "engines" / "v4.py").read_bytes()

    for stem in sys.argv[1:]:
        manual = REPO / "engines" / "manual" / f"{stem}.py"
        print(f"=== {stem} ===")
        if not manual.exists():
            print("  sin manual")
            continue
        try:
            data = finalize(generate._manual_bundle(manual, engine, ""), v4_engine)
        except Exception as exc:
            print(f"  _manual_bundle fallo: {type(exc).__name__}: {exc}")
            continue

        dest = out / f"{stem}.py"
        dest.write_bytes(data)
        try:
            tree = ast.parse(data.decode("utf-8"))
        except SyntaxError as exc:
            print(f"  SYNTAX linea {exc.lineno}: {exc.msg}")
            continue

        classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
        names = [c.name for c in classes]
        dupes = sorted({n for n in names if names.count(n) > 1})
        stubs = [
            c.name
            for c in classes
            if not c.bases and len(c.body) == 1 and isinstance(c.body[0], ast.Pass)
        ]
        print(f"  bytes={len(data)} clases={len(classes)}")
        print(f"  FuenteBaseSource: {'FuenteBaseSource' in names}")
        print(f"  duplicadas      : {dupes or 'ninguna'}")
        print(f"  stubs vacios    : {stubs or 'ninguno'}")
    print(f"\nescrito en {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
