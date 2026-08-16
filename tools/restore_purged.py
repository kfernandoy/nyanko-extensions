"""Restaura manuales que la purga `eedde66` recorto dejandolos sin `pages`.

La purga de manuales fantasma elimino, en varios manuales reales, el motor
inlineado que aportaba `pages` (y a veces otros metodos del contrato). El
manual quedo con solo su logica propia heredando de un stub vacio, asi que la
fuente dejo de cumplir el contrato Source v4.

Modo informe (por defecto) compara el manual actual con su version en el ultimo
commit sano previo a la purga. Con `--apply` restaura los que perdieron `pages`.
"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MANUAL = REPO / "engines" / "manual"

# Ultimo commit sano inmediatamente anterior a la purga `eedde66`.
PRE_PURGE = "2e0fdcb"

HAS_PAGES = re.compile(r"^\s+(?:async )?def pages\b", re.M)
SOURCE_RE = re.compile(r"^SOURCE = (\w+)", re.M)


def git_blob(commit: str, rel: str) -> str | None:
    res = subprocess.run(
        ["git", "show", f"{commit}:{rel}"], capture_output=True, cwd=REPO
    )
    if res.returncode != 0 or not res.stdout:
        return None
    return res.stdout.decode("utf-8", errors="replace")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*", help="manuales a revisar (sin .py)")
    ap.add_argument("--apply", action="store_true", help="restaurar los recortados")
    ap.add_argument(
        "--history",
        action="store_true",
        help="solo informar: rastrea pages/SOURCE por varios commits",
    )
    args = ap.parse_args()

    if args.history:
        commits = ("2e0fdcb", "bcb2a33", "04da9e5", "8af3b48")
        for name in args.names or []:
            rel = f"engines/manual/{name}.py"
            print(f"== {name}")
            cur = (MANUAL / f"{name}.py")
            if cur.exists():
                t = cur.read_text(encoding="utf-8")
                print(
                    f"   ACTUAL : {len(t):>6}B pages={bool(HAS_PAGES.search(t))} "
                    f"SOURCE={SOURCE_RE.findall(t)}"
                )
            for commit in commits:
                blob = git_blob(commit, rel)
                if blob is None:
                    print(f"   {commit}: sin blob")
                    continue
                print(
                    f"   {commit}: {len(blob):>6}B pages={bool(HAS_PAGES.search(blob))} "
                    f"SOURCE={SOURCE_RE.findall(blob)}"
                )
        return 0

    names = args.names or [p.stem for p in sorted(MANUAL.glob("*.py"))]

    restored = 0
    for name in names:
        path = MANUAL / f"{name}.py"
        if not path.exists():
            continue
        cur = path.read_text(encoding="utf-8")
        if HAS_PAGES.search(cur):
            continue

        rel = f"engines/manual/{name}.py"
        old = git_blob(PRE_PURGE, rel)
        if old is None or not HAS_PAGES.search(old):
            continue

        cur_src = SOURCE_RE.findall(cur)
        old_src = SOURCE_RE.findall(old)
        print(
            f"{name}: {len(cur)}B sin pages -> {PRE_PURGE} {len(old)}B con pages "
            f"| SOURCE {cur_src} -> {old_src}"
        )
        if args.apply:
            path.write_text(old, encoding="utf-8")
            restored += 1

    if args.apply:
        print(f"\nrestaurados: {restored}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
