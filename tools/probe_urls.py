"""Comprueba si los sitios de las fuentes responden, antes de juzgar el codigo.

Una fuente puede fallar porque el sitio esta caido, bloquea por Cloudflare o
cambio de dominio: eso NO es un bug del bundle. Este script separa "sitio
inalcanzable" de "fuente rota".

Uso:
    python tools/probe_urls.py                 # todas las pendientes
    python tools/probe_urls.py id1 id2 ...     # concretas
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BACKEND = Path(r"E:\2023-09-04\anitracker\Nyanko\apps\backend")

ATRIBUTOS_URL = (
    "base_url",
    "BASE_URL",
    "site_url",
    "url",
    "domain",
    "_base_url",
)


def cargar_fuente(ext_id: str):
    """Ejecuta el bundle con el backend en sys.path y devuelve la instancia."""
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    path = REPO / "bundles" / f"{ext_id}.py"
    if not path.exists():
        return None, "sin bundle"
    mod = types.ModuleType(ext_id)
    mod.__dict__["__file__"] = str(path)
    try:
        exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), mod.__dict__)
    except Exception as exc:  # noqa: BLE001
        return None, f"EXEC {type(exc).__name__}: {str(exc)[:60]}"
    source = mod.__dict__.get("SOURCE")
    if source is None:
        return None, "sin SOURCE"
    try:
        return source(), None
    except Exception as exc:  # noqa: BLE001
        return None, f"INIT {type(exc).__name__}: {str(exc)[:60]}"


def extraer_url(source) -> str | None:
    for attr in ATRIBUTOS_URL:
        valor = getattr(source, attr, None)
        if isinstance(valor, str) and valor.startswith("http"):
            return valor
    caps = getattr(source, "capabilities", None)
    if isinstance(caps, dict):
        for clave in ("base_url", "site_url", "url"):
            valor = caps.get(clave)
            if isinstance(valor, str) and valor.startswith("http"):
                return valor
    return None


async def sondear(session, ext_id: str, url: str) -> dict:
    import httpx

    resultado = {"id": ext_id, "url": url}
    try:
        resp = await session.get(url, follow_redirects=True, timeout=20)
        resultado["status"] = resp.status_code
        resultado["final"] = str(resp.url)
        resultado["bytes"] = len(resp.content)
        cf = "cloudflare" in resp.headers.get("server", "").lower()
        resultado["cloudflare"] = cf
        if resp.status_code == 200:
            resultado["veredicto"] = "VIVO"
        elif resp.status_code in (403, 503) and cf:
            resultado["veredicto"] = "BLOQUEADO_CF"
        elif resp.status_code in (401, 403):
            resultado["veredicto"] = "BLOQUEADO"
        elif resp.status_code == 404:
            resultado["veredicto"] = "NO_ENCONTRADO"
        else:
            resultado["veredicto"] = f"HTTP_{resp.status_code}"
    except Exception as exc:  # noqa: BLE001
        resultado["status"] = None
        resultado["veredicto"] = "INALCANZABLE"
        resultado["error"] = f"{type(exc).__name__}: {str(exc)[:70]}"
    return resultado


async def principal(ids: list[str]) -> int:
    import httpx

    objetivos: list[tuple[str, str]] = []
    problemas: list[dict] = []

    for ext_id in ids:
        source, error = cargar_fuente(ext_id)
        if source is None:
            problemas.append({"id": ext_id, "veredicto": "BUNDLE_ROTO", "error": error})
            continue
        url = extraer_url(source)
        if not url:
            problemas.append({"id": ext_id, "veredicto": "SIN_URL"})
            continue
        objetivos.append((ext_id, url))

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        )
    }
    limite = asyncio.Semaphore(8)
    resultados: list[dict] = []

    async with httpx.AsyncClient(headers=headers) as session:

        async def tarea(ext_id: str, url: str):
            async with limite:
                resultados.append(await sondear(session, ext_id, url))

        await asyncio.gather(*(tarea(i, u) for i, u in objetivos))

    resultados.extend(problemas)
    resultados.sort(key=lambda r: (r.get("veredicto", ""), r["id"]))

    salida = REPO / ".probe_urls.json"
    salida.write_text(json.dumps(resultados, ensure_ascii=False, indent=1), encoding="utf-8")

    resumen: dict[str, int] = {}
    for r in resultados:
        resumen[r["veredicto"]] = resumen.get(r["veredicto"], 0) + 1
    print("=== SONDEO DE SITIOS ===")
    for clave, valor in sorted(resumen.items(), key=lambda kv: -kv[1]):
        print(f"  {clave:<16} {valor}")
    print(f"\ndetalle en {salida.name}")
    return 0


if __name__ == "__main__":
    argumentos = sys.argv[1:]
    if not argumentos:
        datos = json.loads((REPO / ".validacion.json").read_text(encoding="utf-8"))
        argumentos = [f["id"] for f in datos if not f["ya_revisado"]]
    raise SystemExit(asyncio.run(principal(argumentos)))
