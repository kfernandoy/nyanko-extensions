"""Valida los bundles EXACTAMENTE como lo hace el backend al instalarlos.

Replica la cadena real de `nyanko_api/extension_loader.py`:
    compile() -> exec() -> getattr(SOURCE|build_source) -> build_source_registry()
y reporta el mismo motivo de rechazo que produce el 422
"el bundle no supera el contrato de fuente".

Requiere poder importar `nyanko_api`. Se le pasa la ruta del backend con
--backend o la variable NYANKO_BACKEND. Si no esta disponible, cae a un
modo degradado que solo comprueba compile() + presencia de miembros del Protocol.

Uso:
    python tools/verify_bundles.py
    python tools/verify_bundles.py --backend ..\\Nyanko\\apps\\backend
    python tools/verify_bundles.py --only anzmanga_es,akuma_es -v
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_BACKEND = REPO.parent / "Nyanko" / "apps" / "backend"


def _load_contract(backend: Path):
    """Devuelve (Source, build_source_registry) del backend, o (None, None)."""
    if not (backend / "nyanko_api" / "sources" / "contract.py").exists():
        return None, None
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    try:
        from nyanko_api.sources import build_source_registry  # type: ignore
        from nyanko_api.sources.contract import Source  # type: ignore
    except Exception:
        return None, None
    return Source, build_source_registry


def _exec_bundle(path: Path):
    """compile + exec igual que el loader. Devuelve el modulo ejecutado."""
    module_name = f"nyanko_installed_source_{path.stem}"
    spec = importlib.util.spec_from_loader(module_name, loader=None)
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(path)
    code = compile(path.read_bytes(), str(path), "exec")
    sys.modules[module_name] = module
    try:
        exec(code, module.__dict__)
    finally:
        sys.modules.pop(module_name, None)
    return module


def verify(path: Path, Source, build_source_registry, verbose: bool) -> tuple[str, str]:
    """Devuelve (estado, detalle). estado in {ok, syntax, exec, nofactory, contract}."""
    try:
        module = _exec_bundle(path)
    except SyntaxError as exc:
        return "syntax", f"linea {exc.lineno}: {exc.msg}"
    except Exception as exc:
        detail = traceback.format_exc(limit=3) if verbose else f"{type(exc).__name__}: {exc}"
        return "exec", detail

    factory = getattr(module, "SOURCE", None) or getattr(module, "build_source", None)
    if factory is None:
        return "nofactory", "no declara SOURCE ni build_source"

    # Modo degradado: sin backend solo comprobamos el Protocol por miembros.
    if build_source_registry is None:
        try:
            instance = factory()
        except Exception as exc:
            return "exec", f"al instanciar: {type(exc).__name__}: {exc}"
        required = (
            "name", "display_name", "api_version", "capabilities",
            "search", "browse", "chapters", "pages", "page_bytes",
        )
        missing = [m for m in required if not hasattr(instance, m)]
        if missing:
            return "contract", f"falta {missing}"
        return "ok", ""

    try:
        registrations = build_source_registry(sources=[factory]).registrations()
    except Exception as exc:
        return "contract", f"registry fallo: {type(exc).__name__}: {exc}"

    if len(registrations) != 1:
        return "contract", f"registrations inesperadas: {len(registrations)}"

    reg = registrations[0]
    if reg.status != "ok" or reg.source is None:
        reason = getattr(reg, "rejection_reason", None) or reg.status
        return "contract", str(reason)
    return "ok", ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default=str(DEFAULT_BACKEND))
    ap.add_argument("--only", default="", help="lista separada por comas de ids")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    Source, registry = _load_contract(Path(args.backend))
    if registry is None:
        print(f"[aviso] backend no importable en {args.backend}; modo degradado\n")
    else:
        print(f"[ok] contrato real cargado desde {args.backend}\n")

    files = sorted((REPO / "bundles").glob("*.py"))
    if args.only:
        wanted = {s.strip() for s in args.only.split(",") if s.strip()}
        files = [f for f in files if f.stem in wanted]

    buckets: dict[str, list[tuple[str, str]]] = {}
    for path in files:
        status, detail = verify(path, Source, registry, args.verbose)
        buckets.setdefault(status, []).append((path.stem, detail))

    total = len(files)
    ok = len(buckets.get("ok", []))
    print(f"total {total} | ok {ok} | fallan {total - ok}\n")

    labels = {
        "syntax": "NO COMPILAN",
        "exec": "PETAN AL EJECUTAR",
        "nofactory": "SIN SOURCE/build_source",
        "contract": "INCUMPLEN CONTRATO (-> 422)",
    }
    for key, label in labels.items():
        rows = buckets.get(key, [])
        if not rows:
            continue
        print(f"== {label}: {len(rows)} ==")
        for name, detail in rows[: (len(rows) if args.verbose else 20)]:
            print(f"  {name}: {detail}")
        if not args.verbose and len(rows) > 20:
            print(f"  ... y {len(rows) - 20} mas")
        print()

    return 1 if ok != total else 0


if __name__ == "__main__":
    raise SystemExit(main())
