"""Busca fuentes que delaten el hotlink al pedir imagenes a un CDN de terceros.

El caso onfmangas: las paginas eran hotlinks a la red de MangaDex, y mandarle el `Referer`
del sitio hacia que el CDN devolviera un 200 con una imagen-aviso ("you can read this at
mangadex.org") de 59 KB en vez de la pagina real de ~700 KB. Como responde 200 y con
content-type de imagen, ningun chequeo de status lo detecta: solo se ve comparando el
tamano con y sin Referer.

Este script resuelve una pagina real de cada fuente y la pide dos veces, con y sin el
Referer del sitio. Si el tamano cambia de forma significativa, el sitio esta discriminando
por Referer y hay que decidir cual mandar.

Uso: python tools/barrido_referer.py [--ids a,b,c] [--lang es] [--limite 40]
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import pathlib
import re
import sys
import warnings
from urllib.parse import urlparse

import httpx

ROOT = pathlib.Path(__file__).resolve().parent.parent
UA = (
    "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Mobile Safari/537.36"
)
# Diferencia relativa a partir de la cual se considera que el CDN sirve otra cosa.
UMBRAL = 0.20


class Fetcher:
    def __init__(self, client: httpx.AsyncClient, headers: dict) -> None:
        self.client = client
        self.headers = headers

    async def request(self, method: str, url: str, **kwargs):
        merged = {**self.headers, **dict(kwargs.pop("headers", None) or {})}
        return await self.client.request(method, url, headers=merged, **kwargs)


def cargar(extension_id: str):
    ruta = ROOT / "bundles" / f"{extension_id}.py"
    spec = importlib.util.spec_from_file_location(f"barrido_{extension_id}", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo.SOURCE


def referer_declarado(extension_id: str) -> str:
    texto = (ROOT / "bundles" / f"{extension_id}.py").read_text(
        encoding="utf-8", errors="replace",
    )
    # Ojo con el f-string `{"Referer": f"{self.base_url}/"}`: un `[^}]*` se corta en la
    # primera llave interna y se pierde la mitad de los bundles. Se busca la clave directa.
    bloque = re.search(
        r"image_headers\s*=\s*\{.{0,400}?['\"]Referer['\"]\s*:\s*(f?)['\"]([^'\"]*)['\"]",
        texto,
        re.S,
    )
    if not bloque:
        return ""
    valor = bloque.group(2)
    # Los que lo derivan de `base_url` se resuelven al instanciar la fuente; aqui basta
    # con marcar que declaran Referer.
    return valor or "{base_url}"


async def revisar(extension_id: str, timeout: float) -> dict | None:
    resultado = {"id": extension_id, "referer": referer_declarado(extension_id)}
    try:
        factory = cargar(extension_id)
        fuente = factory()
        cabeceras = {"User-Agent": UA, **dict(fuente.capabilities.headers)}
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True, verify=False,
        ) as cliente:
            fuente = factory(Fetcher(cliente, cabeceras))
            listado = await fuente.browse("popular", 1)
            # `browse` puede devolver Paginated o dict segun el adaptador; el `or` con
            # `.get` reventaba en cuanto Paginated traia la lista vacia.
            items = (
                listado.get("items", [])
                if isinstance(listado, dict)
                else getattr(listado, "items", []) or []
            )
            if not items:
                return {**resultado, "estado": "sin series"}

            pagina = None
            # La primera serie puede no tener capitulos legibles, y el primer capitulo
            # puede estar tras un muro de pago o vacio: se insiste un poco antes de darla
            # por muerta, que si no el barrido reporta "sin paginas" en fuentes sanas.
            for serie in items[:5]:
                try:
                    capitulos = await fuente.chapters(serie.source_id)
                except Exception:
                    continue
                for capitulo in (capitulos or [])[:3]:
                    try:
                        paginas = await fuente.pages(capitulo.source_id)
                    except Exception:
                        continue
                    if paginas:
                        pagina = paginas[0]
                        break
                if pagina is not None:
                    break
            if pagina is None:
                return {**resultado, "estado": "sin paginas"}

            url = pagina.source_id.partition("#")[0]
            host_imagen = urlparse(url).netloc
            host_sitio = urlparse(fuente.base_url).netloc
            resultado["host_imagen"] = host_imagen
            # Solo interesa cuando la imagen NO la sirve el propio sitio: si es su dominio,
            # mandar su Referer es lo correcto y no hay hotlink que delatar.
            propio = host_imagen.endswith(host_sitio.removeprefix("www.")) or host_sitio.endswith(
                host_imagen.removeprefix("www."),
            )
            resultado["cdn_ajeno"] = not propio

            async def pedir(referer: str | None) -> tuple:
                cab = {"User-Agent": UA}
                if referer:
                    cab["Referer"] = referer
                try:
                    respuesta = await cliente.get(url, headers=cab)
                    return respuesta.status_code, len(respuesta.content)
                except Exception as error:
                    return type(error).__name__, 0

            declarado = resultado["referer"]
            # Los que lo derivan de `base_url` traen la plantilla sin resolver.
            base = declarado if declarado and "{" not in declarado else f"https://{host_sitio}/"
            con_estado, con_bytes = await pedir(base)
            sin_estado, sin_bytes = await pedir(None)
            resultado |= {
                "con_referer": f"{con_estado}/{con_bytes}",
                "sin_referer": f"{sin_estado}/{sin_bytes}",
            }
            mayor = max(con_bytes, sin_bytes)
            distinto = bool(mayor) and abs(con_bytes - sin_bytes) / mayor > UMBRAL
            # Solo es el patron onfmangas cuando MANDAR el Referer empeora la respuesta:
            # el CDN contesta 200 con una imagen-aviso mas pequena que la real. Si sin
            # Referer sale peor (403 o imagen recortada) es proteccion de hotlink normal y
            # el Referer hace falta: se marca aparte para no confundir los dos casos.
            peor_con_referer = distinto and con_bytes < sin_bytes and con_estado == 200
            resultado["sospechoso"] = bool(not propio and peor_con_referer)
            resultado["referer_necesario"] = bool(not propio and distinto and not peor_con_referer)
            resultado["estado"] = "ok"
            return resultado
    except Exception as error:
        return {**resultado, "estado": f"ERR {type(error).__name__}: {error}"[:120]}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ids", default="")
    parser.add_argument("--lang", default="")
    parser.add_argument("--limite", type=int, default=40)
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--timeout", type=float, default=40.0)
    parser.add_argument("--out", default="barrido_referer.json")
    args = parser.parse_args()

    indice = json.loads((ROOT / "index.json").read_text(encoding="utf-8"))["extensions"]
    candidatos: list[str] = []
    for extension in indice:
        if args.lang and not str(extension.get("language", "")).lower().startswith(args.lang):
            continue
        if not referer_declarado(extension["id"]):
            continue
        candidatos.append(extension["id"])
    if args.ids:
        pedidos = {valor.strip() for valor in args.ids.split(",") if valor.strip()}
        candidatos = [item for item in candidatos if item in pedidos]
    candidatos = candidatos[: args.limite]
    print(f"revisando {len(candidatos)} fuentes con Referer declarado")

    semaforo = asyncio.Semaphore(args.concurrency)
    hechos = 0

    async def tarea(extension_id: str):
        nonlocal hechos
        async with semaforo:
            salida = await revisar(extension_id, args.timeout)
            hechos += 1
            print(f"[{hechos}/{len(candidatos)}] {extension_id}")
            return salida

    filas = [fila for fila in await asyncio.gather(*(tarea(i) for i in candidatos)) if fila]
    (ROOT / args.out).write_text(json.dumps(filas, indent=2), encoding="utf-8")

    sospechosos = [f for f in filas if f.get("sospechoso")]
    necesario = [f for f in filas if f.get("referer_necesario")]
    ajenos = [
        f for f in filas
        if f.get("cdn_ajeno") and not f.get("sospechoso") and not f.get("referer_necesario")
    ]
    print(f"\n=== SOSPECHOSOS: el Referer del sitio EMPEORA la imagen ({len(sospechosos)}) ===")
    for fila in sospechosos:
        print(f"  {fila['id']:<28} {fila['host_imagen']:<34} con={fila['con_referer']} sin={fila['sin_referer']}")
    print(f"\n=== El Referer es NECESARIO, no tocar ({len(necesario)}) ===")
    for fila in necesario:
        print(f"  {fila['id']:<28} {fila['host_imagen']:<34} con={fila['con_referer']} sin={fila['sin_referer']}")
    print(f"\n=== CDN ajeno, indiferente al Referer ({len(ajenos)}) ===")
    for fila in ajenos[:20]:
        print(f"  {fila['id']:<28} {fila['host_imagen']}")
    print(f"\nEscrito {args.out}")


if __name__ == "__main__":
    warnings.filterwarnings("ignore")
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
