"""Muestra el motor/tema declarado en index.json para las extensiones dadas."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def main(names: list[str]) -> int:
    data = json.loads((REPO / "index.json").read_text(encoding="utf-8"))
    index = {e["id"]: e for e in data["extensions"]}
    for name in names:
        entry = index.get(name)
        if entry is None:
            print(f"{name:<28} NO ESTA EN EL INDICE")
            continue
        engine = entry.get("engine")
        theme = entry.get("theme")
        bundle = REPO / "bundles" / f"{name}.py"
        size = bundle.stat().st_size if bundle.exists() else 0
        print(f"{name:<28} engine={str(engine):<14} theme={str(theme):<16} bundle={size}B")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
