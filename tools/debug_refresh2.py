"""Ejecuta `_refrescar_motor_en_manual` tal como lo hace generate.py.

Reproduce el motor exacto que recibe cada extension segun su `engine` en
index.json y comprueba si el refresco realmente inyecta el motor o devuelve el
manual intacto (lo que deja el stub vacio en el bundle).
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location("gen", REPO / "tools" / "generate.py")
gen = importlib.util.module_from_spec(spec)
sys.modules["gen"] = gen
spec.loader.exec_module(gen)


def read_engine(name: str) -> str:
    return (REPO / "engines" / f"{name}.py").read_text(encoding="utf-8")


def main(names: list[str]) -> int:
    data = json.loads((REPO / "index.json").read_text(encoding="utf-8"))
    index = {e["id"]: e for e in data["extensions"]}
    base = read_engine("base")

    for name in names:
        entry = index.get(name, {})
        engine_name = entry.get("engine")
        manual = (REPO / "engines" / "manual" / f"{name}.py").read_text(encoding="utf-8")

        if engine_name in {"galleryadults", "mangathemesia", "moonlighttl"}:
            engine = base.rstrip() + "\n\n" + read_engine(engine_name).rstrip()
        else:
            engine = read_engine("madara")

        out = gen._refrescar_motor_en_manual(manual, engine)
        sin_tocar = out.strip() == manual.strip()
        stubs = re.findall(r"^class (\w+)\s*:\s*\n\s*pass", out, re.M)
        print(f"== {name}  engine={engine_name}")
        print(f"   manual {len(manual)}B -> refrescado {len(out)}B")
        print(f"   DEVUELTO SIN TOCAR: {sin_tocar}")
        print(f"   stubs vacios que quedan: {stubs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
