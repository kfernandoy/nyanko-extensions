"""Valida los bundles regenerados en .tmp_regen/ contra el contrato real."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

from verify_bundles import _load_contract, verify  # noqa: E402

BACKEND = REPO.parent / "Nyanko" / "apps" / "backend"


def main() -> int:
    Source, registry = _load_contract(BACKEND)
    if registry is None:
        print("[aviso] backend no importable; modo degradado")

    files = sorted((REPO / ".tmp_regen").glob("*.py"))
    if not files:
        print("no hay nada en .tmp_regen/")
        return 1

    fails = 0
    for path in files:
        status, detail = verify(path, Source, registry, verbose=True)
        mark = "OK  " if status == "ok" else "FAIL"
        if status != "ok":
            fails += 1
        print(f"  {mark} {path.stem}: {status} {detail}".rstrip())
    print(f"\n{len(files) - fails}/{len(files)} pasan el contrato")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
