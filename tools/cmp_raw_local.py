"""Compara los bundles publicados en raw.githubusercontent con los locales.

La app instala desde el RAW de GitHub, no desde el arbol local: verificar solo
en local no dice nada sobre lo que ven los usuarios.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BASE = "https://raw.githubusercontent.com/kfernandoy/nyanko-extensions/main/"

SOURCE_RE = re.compile(r"^SOURCE = (.+)$", re.M)
STUB_RE = re.compile(r"^class \w+\s*:\s*\n\s+pass\s*$", re.M)
PAGES_RE = re.compile(r"^\s+(?:async )?def pages\b", re.M)


def fetch(path: str) -> bytes:
    with urllib.request.urlopen(BASE + path, timeout=30) as resp:
        return resp.read()


def describe(text: str) -> str:
    src = SOURCE_RE.findall(text)
    return (
        f"{len(text):>7}B "
        f"pages={bool(PAGES_RE.search(text))!s:<5} "
        f"stub={bool(STUB_RE.search(text))!s:<5} "
        f"SOURCE={src[:1]}"
    )


def main(names: list[str]) -> int:
    for name in names:
        local_path = REPO / "bundles" / f"{name}.py"
        local = local_path.read_text(encoding="utf-8") if local_path.exists() else ""
        try:
            remote = fetch(f"bundles/{name}.py").decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            print(f"{name}: ERROR al descargar -> {exc}")
            continue
        print(f"== {name}")
        print(f"   RAW   {describe(remote)}")
        print(f"   LOCAL {describe(local) if local else 'NO EXISTE'}")
        print(f"   identicos: {remote == local}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or ["akuma_es", "emperorscan_es", "hentaienvy_es"]))
