"""Clasifica por que una extension no devuelve series: sitio caido o port roto.

`smoke.py` dice que una fuente da 0 resultados, pero no si la culpa es del sitio (dominio
vendido, Cloudflare, 404) o del parseo. La diferencia importa: lo primero se retira del
indice, lo segundo se arregla.

Para cada fuente pide su `base_url` en crudo y compara con lo que devuelve `browse`:

  MUERTA        el dominio no resuelve, o responde 4xx/5xx
  PROTEGIDA     responde, pero con un reto de Cloudflare/DDoS-Guard
  PARSER_ROTO   el sitio sirve HTML normal y aun asi browse devuelve 0  <- accionable
  OK            devuelve series

Uso: python tools/triaje.py [--lang es] [--limite 200] [--solo-vacias]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import re
import sys
import warnings

import httpx

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from barrido_referer import UA, Fetcher, cargar  # noqa: E402

# Señales de que responde una pasarela anti-bot y no el sitio.
RETOS = (
    "just a moment",
    "checking your browser",
    "cf-browser-verification",
    "ddos-guard",
    "verificando",
    "__cf_chl",
    "cf_chl_opt",
)
# Por debajo de esto no hay catalogo que parsear: es una landing o un error.
MINIMO_HTML = 2000


def base_url(extension_id: str) -> str:
    texto = (ROOT / "bundles" / f"{extension_id}.py").read_text(
        encoding="utf-8", errors="replace",
    )
    encontrados = re.findall(r"base_url\s*=\s*['\"]([^'\"]+)['\"]", texto)
    # El ultimo es el de la clase concreta; los previos son de las clases heredadas que
    # el bundle arrastra del motor.
    return encontrados[-1] if encontrados else ""


async def triar(extension_id: str, timeout: float) -> dict:
    fila = {"id": extension_id, "base_url": base_url(extension_id)}
    if not fila["base_url"]:
        # Las fuentes de API (MangaDex y similares) no declaran base_url. No se puede
        # sondear el sitio, pero si preguntarle al port si devuelve series.
        try:
            factory = cargar(extension_id)
            fuente = factory()
            async with httpx.AsyncClient(
                timeout=timeout, follow_redirects=True, verify=False,
            ) as cliente:
                cabeceras = {"User-Agent": UA, **dict(fuente.capabilities.headers)}
                fuente = factory(Fetcher(cliente, cabeceras))
                listado = await fuente.browse("popular", 1)
                items = (
                    listado.get("items", [])
                    if isinstance(listado, dict)
                    else getattr(listado, "items", []) or []
                )
            fila["series"] = len(items)
            return {**fila, "veredicto": "OK" if items else "PARSER_ROTO", "detalle": "sin base_url (API)"}
        except Exception as error:
            return {**fila, "veredicto": "ERROR_PORT", "detalle": f"{type(error).__name__}: {error}"[:90]}

    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=True, verify=False,
    ) as cliente:
        # 1. ¿Esta vivo el sitio?
        try:
            respuesta = await cliente.get(fila["base_url"], headers={"User-Agent": UA})
            fila["http"] = respuesta.status_code
            fila["bytes"] = len(respuesta.text)
            cuerpo = respuesta.text[:20000].casefold()
        except Exception as error:
            return {**fila, "veredicto": "MUERTA", "detalle": type(error).__name__}

        # El reto se comprueba ANTES que el status: Cloudflare y DDoS-Guard responden 403
        # al reto, y darlo por sitio muerto marcaba como caidas fuentes que el usuario
        # usa sin problema desde la app (celestialmoon, hentaienvy, akuma).
        servidor = respuesta.headers.get("server", "").casefold()
        protegida = (
            any(senal in cuerpo for senal in RETOS)
            or "ddos-guard" in servidor
            or "cf-mitigated" in {k.casefold() for k in respuesta.headers}
        )
        if protegida:
            return {**fila, "veredicto": "PROTEGIDA", "detalle": f"reto anti-bot ({servidor or '?'})"}
        if respuesta.status_code >= 400:
            return {**fila, "veredicto": "MUERTA", "detalle": f"HTTP {respuesta.status_code}"}

        # 2. ¿Devuelve series el port?
        try:
            factory = cargar(extension_id)
            fuente = factory()
            cabeceras = {"User-Agent": UA, **dict(fuente.capabilities.headers)}
            fuente = factory(Fetcher(cliente, cabeceras))
            total = 0
            for tipo in ("popular", "latest"):
                try:
                    listado = await fuente.browse(tipo, 1)
                except Exception:
                    continue
                items = (
                    listado.get("items", [])
                    if isinstance(listado, dict)
                    else getattr(listado, "items", []) or []
                )
                total = max(total, len(items))
            fila["series"] = total
        except Exception as error:
            return {**fila, "veredicto": "ERROR_PORT", "detalle": f"{type(error).__name__}: {error}"[:90]}

    if fila.get("series"):
        return {**fila, "veredicto": "OK"}
    if fila["bytes"] < MINIMO_HTML:
        return {**fila, "veredicto": "MUERTA", "detalle": f"HTML de {fila['bytes']} B"}
    return {**fila, "veredicto": "PARSER_ROTO"}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lang", default="")
    parser.add_argument("--ids", default="")
    parser.add_argument("--limite", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--out", default="triaje.json")
    args = parser.parse_args()

    indice = json.loads((ROOT / "index.json").read_text(encoding="utf-8"))["extensions"]
    candidatos = [
        item["id"]
        for item in indice
        if not args.lang or str(item.get("language", "")).lower().startswith(args.lang)
    ]
    if args.ids:
        pedidos = {v.strip() for v in args.ids.split(",") if v.strip()}
        candidatos = [i for i in candidatos if i in pedidos]
    candidatos = candidatos[: args.limite]
    print(f"triando {len(candidatos)} extensiones")

    semaforo = asyncio.Semaphore(args.concurrency)
    hechas = 0

    async def tarea(extension_id: str):
        nonlocal hechas
        async with semaforo:
            fila = await triar(extension_id, args.timeout)
            hechas += 1
            if hechas % 20 == 0:
                print(f"  {hechas}/{len(candidatos)}")
            return fila

    filas = await asyncio.gather(*(tarea(i) for i in candidatos))
    (ROOT / args.out).write_text(json.dumps(filas, indent=2), encoding="utf-8")

    grupos: dict[str, list] = {}
    for fila in filas:
        grupos.setdefault(fila["veredicto"], []).append(fila)
    print("\n=== RESUMEN ===")
    for veredicto in ("OK", "PARSER_ROTO", "MUERTA", "PROTEGIDA", "ERROR_PORT", "SIN_BASE_URL"):
        cuantas = len(grupos.get(veredicto, []))
        if cuantas:
            print(f"  {veredicto:<14} {cuantas}")
    print("\n=== PARSER_ROTO (accionable: el sitio responde y aun asi 0 series) ===")
    for fila in grupos.get("PARSER_ROTO", [])[:40]:
        print(f"  {fila['id']:<28} {fila['base_url']:<40} {fila['bytes']} B")
    print(f"\nEscrito {args.out}")


if __name__ == "__main__":
    warnings.filterwarnings("ignore")
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
