"""Ejercita cada extension contra su sitio real y reporta que carril funciona.

Uso: python tools/smoke.py [--lang es] [--only id,id] [--concurrency 8] [--timeout 90] [--samples-per-engine 15]

Recorre browse(popular), browse(latest), search, details, chapters, pages y
page_bytes, parando en el primer fallo de la cadena que depende del anterior.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import pathlib
import sys
import time
import random
from collections import defaultdict
from urllib.parse import urlencode

import httpx

ROOT = pathlib.Path(__file__).resolve().parents[1]

# Cargar interceptor Cloudflare de la app si esta en el PYTHONPATH
try:
    from nyanko_api.cloudflare import AlmacenDeClearances, es_reto_de_cloudflare
    _ALMACEN_CF = AlmacenDeClearances(ROOT.parent / "Nyanko" / "apps" / "backend" / "data" / "cloudflare_clearances.json")
except ImportError:
    _ALMACEN_CF = None
    def es_reto_de_cloudflare(response): return False

# User-Agent de navegador movil, igual que hace Mihon. httpx manda por defecto
# `python-httpx/x.y`, que muchos sitios rechazan de plano.
DEFAULT_UA = (
    "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Mobile Safari/537.36"
)

DDG_WELL_KNOWN = "https://check.ddos-guard.net/check.js"


async def resolver_ddos_guard(client: httpx.AsyncClient, url: str) -> bool:
    """Obtiene la cookie `__ddg2_` que DDoS-Guard exige, como el DDosGuardInterceptor de Mihon.

    NO es saltarse una proteccion: es el handshake que el propio DDoS-Guard publica para
    clientes no-navegador. Mihon lo implementa igual (DDosGuardInterceptor.kt) y por eso
    estas fuentes le cargan y a nosotros nos daban 403.

    Devuelve True si obtuvo cookie nueva y merece la pena reintentar.
    """
    try:
        js = (await client.get(DDG_WELL_KNOWN)).text
        if "'" not in js:
            return False
        ruta = js.split("'")[1]
        if not ruta:
            return False
        host = httpx.URL(url).host
        respuesta = await client.get(f"https://{host}{ruta}")
        # La cookie queda en el jar del cliente; basta con que el check respondiera bien.
        return respuesta.status_code == 200 and "__ddg2_" in respuesta.headers.get(
            "set-cookie", ""
        )
    except Exception:
        return False


class Fetcher:
    """Contrato SourceFetcher sobre httpx, respetando el rpm de la fuente."""


    def __init__(self, client: httpx.AsyncClient, headers: dict, rpm: int) -> None:
        self.client = client
        self.headers = headers
        self.interval = 60.0 / max(rpm, 1)
        self.last = 0.0
        self.lock = asyncio.Lock()
        self.count = 0
        # El reto de DDoS-Guard se resuelve una sola vez por sesion: la cookie queda en el
        # jar del cliente. Sin esta guarda, un 403 persistente reintentaria en bucle.
        self._ddg_intentado = False

    async def request(self, method: str, url: str, **kwargs):
        async with self.lock:
            wait = self.interval - (time.monotonic() - self.last)
            if wait > 0:
                await asyncio.sleep(wait)
            self.last = time.monotonic()
        self.count += 1
        merged = {**self.headers, **dict(kwargs.pop("headers", None) or {})}

        # httpx trata `data=[("a","1"), ...]` como contenido CRUDO y genera un
        # IteratorByteStream (sincrono), que AsyncClient rechaza con
        # "Attempted to send an sync request with an AsyncClient instance".
        # Varios bundles pasan listas de tuplas porque los formularios de WordPress repiten
        # claves (`vars[meta_query][0][key]`), asi que se normaliza aqui: httpx solo produce
        # el ByteStream asincrono correcto cuando `data` es un mapping.
        datos = kwargs.get("data")
        if isinstance(datos, (list, tuple)) and all(
            isinstance(par, (list, tuple)) and len(par) == 2 for par in datos
        ):
            # `urlencode` conserva las claves repetidas, que es justo lo que la lista de
            # tuplas venia a expresar; convertirla a dict las perderia.
            kwargs["content"] = urlencode([(str(k), str(v)) for k, v in datos])
            kwargs.pop("data")
            merged.setdefault("Content-Type", "application/x-www-form-urlencoded")

        # Inyectar Cloudflare resolver si tenemos almacen y esta resuelto
        if _ALMACEN_CF:
            clearance = _ALMACEN_CF.obtener(url)
            if clearance:
                if clearance.caducada:
                    _ALMACEN_CF.invalidar(url)
                else:
                    k_dict = clearance.aplicar(kwargs)
                    for hdr_key, hdr_val in k_dict.get("headers", {}).items():
                        merged[hdr_key] = hdr_val
                    kwargs.pop("headers", None)
                    if "cookies" in k_dict:
                        kwargs["cookies"] = k_dict["cookies"]

        respuesta = await self.client.request(method, url, headers=merged, **kwargs)

        # Resolver Cloudflare si nos encontramos con el reto
        if _ALMACEN_CF and es_reto_de_cloudflare(respuesta):
            clearance = await _ALMACEN_CF.resolver(url)
            if clearance:
                k_dict = clearance.aplicar(kwargs)
                for hdr_key, hdr_val in k_dict.get("headers", {}).items():
                    merged[hdr_key] = hdr_val
                kwargs.pop("headers", None)
                if "cookies" in k_dict:
                    kwargs["cookies"] = k_dict["cookies"]
                self.count += 1
                respuesta = await self.client.request(method, url, headers=merged, **kwargs)

        # Mismo criterio que Mihon: 403 + `Server: ddos-guard` -> resolver el reto UNA vez y
        # reintentar. Se hace aqui y no en cada bundle porque el reto es del proveedor, no
        # de la fuente: cualquier extension detras de DDoS-Guard lo necesita igual.
        if (
            respuesta.status_code == 403
            and "ddos-guard" in respuesta.headers.get("server", "").lower()
            and not self._ddg_intentado
        ):
            self._ddg_intentado = True
            if await resolver_ddos_guard(self.client, url):
                self.count += 1
                respuesta = await self.client.request(method, url, headers=merged, **kwargs)

        return respuesta

def load(extension_id: str):
    path = ROOT / "bundles" / f"{extension_id}.py"
    spec = importlib.util.spec_from_file_location(f"smoke_{extension_id}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SOURCE

def brief(error: BaseException) -> str:
    text = f"{type(error).__name__}: {error}".replace("\n", " ").strip()
    return text[:160]

# Cuantas series/capitulos se prueban antes de declarar rota una fuente. Bajo a proposito:
# cada intento es una peticion real al sitio, y el objetivo es descartar el falso negativo
# de "la primera serie no tenia capitulos en este idioma", no recorrer el catalogo.
MAX_SERIES_INTENTOS = 3
# Se prueban mas capitulos que series porque los sitios con muro de pago suelen bloquear
# los mas recientes en bloque: en asialotus los 3 primeros devuelven 0 paginas y solo a
# partir del cuarto aparece el 402 que permite clasificarlo como `paywall` en vez de como
# un port roto. Siguen siendo pocas peticiones porque el bucle corta en cuanto una funciona.
MAX_CAPITULOS_INTENTOS = 6


async def probe(extension_id: str, timeout: float, engine: str) -> dict:
    result = {"id": extension_id, "engine": engine, "steps": {}, "requests": 0}
    try:
        factory = load(extension_id)
    except Exception as error:
        result["steps"]["load"] = {"status": "error", "error": brief(error)}
        return result

    source = factory()
    limits = httpx.Limits(max_connections=4)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(25.0),
        follow_redirects=True,
        limits=limits,
        verify=False,
    ) as client:
        # El UA de la extension MANDA sobre el default: algunas fuentes exigen uno concreto.
        # El default solo cubre el hueco de las que no declaran ninguno, que con el
        # `python-httpx/x.y` de fabrica eran rechazadas antes de parsear nada.
        cabeceras = {"User-Agent": DEFAULT_UA, **dict(source.capabilities.headers)}
        fetcher = Fetcher(
            client,
            cabeceras,
            source.capabilities.requests_per_minute,
        )
        source = factory(fetcher)

        async def step(name: str, coro):
            try:
                value = await asyncio.wait_for(coro, timeout=timeout)
            except Exception as error:
                result["steps"][name] = {"status": "error", "error": brief(error)}
                return None
            
            items = getattr(value, "items", None)
            if items is not None:
                covers = sum(1 for i in items if getattr(i, "cover_url", None))
                result["steps"][name] = {"status": "ok", "items": len(items), "cover": covers}
            else:
                if name == "details" and value:
                    v_dict = {"status": "ok"}
                    for f in ["cover_url", "description", "author", "artist", "status", "content_tags"]:
                        val = getattr(value, f, None)
                        v_dict[f] = 1 if val else 0
                    result["steps"][name] = v_dict
                elif name == "chapters" and value:
                    result["steps"][name] = {"status": "ok", "items": len(value)}
                elif name == "pages" and value:
                    result["steps"][name] = {"status": "ok", "items": len(value)}
                else:
                    result["steps"][name] = {"status": "ok"}
            return value

        popular = await step("popular", source.browse("popular", 1))
        latest = await step("latest", source.browse("latest", 1))
        
        # safely handle search which may fail
        try:
            await step("search", source.search("a", 1, None))
        except Exception:
            pass

        items = []
        for candidate in (popular, latest):
            if candidate is not None and getattr(candidate, "items", None):
                items = candidate.items
                break
                
        if not items:
            result["requests"] = fetcher.count
            return result
        result["sample"] = items[0].source_id

        # Se prueban VARIAS series, no solo la primera.
        #
        # Una serie sin capitulos NO significa fuente rota: en las de MangaDex por idioma, el
        # catalogo popular es global, asi que la serie mas seguida puede no tener ni una
        # traduccion al idioma de la extension y el feed devuelve 0 legitimamente. Probando
        # solo `items[0]` se marcaba como IMPLEMENTATION_REQUIRED una extension sana
        # (mangadex_es: 0 capitulos en la primera serie, 343 en Berserk).
        #
        # Solo si NINGUNA de la muestra da capitulos se considera un fallo real de la fuente.
        chapters = None
        series = items[0]
        for candidato in items[:MAX_SERIES_INTENTOS]:
            series = candidato
            if hasattr(source, "details"):
                detailed = await step("details", source.details(candidato))
                series = detailed or candidato

            chapters = await step("chapters", source.chapters(series))
            if chapters:
                result["sample"] = candidato.source_id
                break

        if not chapters:
            result["requests"] = fetcher.count
            return result
        result["chapters"] = len(chapters)

        # Mismo razonamiento para las paginas: un capitulo concreto puede estar vacio (en
        # MangaDex, los marcados `empty` son entradas de solo-enlace externo) sin que la
        # fuente falle. Se prueban varios antes de declararlo roto.
        #
        # `page_bytes` se prueba DENTRO de este bucle, no despues sobre el primer capitulo.
        # Antes se descargaba siempre `chapters[0]`, y eso da un falso negativo sistematico
        # en las fuentes que blindan solo el capitulo mas reciente: bloomscans sirve las
        # imagenes de 135 de 136 capitulos, pero el ultimo pasa por su "Bloom Reader Guard"
        # y devuelve 404, asi que la extension entera se marcaba IMPLEMENTATION_REQUIRED
        # estando sana. Lo que se quiere medir es "esta fuente entrega imagenes", no
        # "entrega EL capitulo mas nuevo".
        pages = None
        content = None
        capitulo_ok = None
        fallo_page_bytes = None
        for capitulo in chapters[:MAX_CAPITULOS_INTENTOS]:
            candidatas = await step("pages", source.pages(capitulo))
            if not candidatas:
                continue
            pages = pages or candidatas
            content = await step("page_bytes", source.page_bytes(candidatas[0]))
            if content is not None:
                pages = candidatas
                capitulo_ok = capitulo
                break
            # Guardar el motivo del ultimo intento fallido. Sin esto, el paso `pages` de
            # una iteracion posterior pisa el error de `page_bytes` y el clasificador
            # pierde el 402 que distingue un muro de pago de un parser roto.
            fallo_page_bytes = result["steps"].get("page_bytes")

        if content is None and fallo_page_bytes is not None:
            result["steps"]["page_bytes"] = fallo_page_bytes

        if not pages:
            result["requests"] = fetcher.count
            return result
        result["pages"] = len(pages)
        if capitulo_ok is not None:
            result["sample_chapter"] = capitulo_ok.source_id

        if content is not None:
            try:
                size = sum(len(chunk) for chunk in (content.chunks or []))
                result["bytes"] = size
                base_dict = {"status": "ok", "bytes": size}
                if size < 512:
                    base_dict["warning"] = f"solo {size} bytes"
                result["steps"]["page_bytes"] = base_dict
            except Exception as error:
                result["steps"]["page_bytes"] = {"status": "error", "error": brief(error)}
                
    result["requests"] = fetcher.count
    return result

async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lang", default="es")
    parser.add_argument("--only", default="")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--samples-per-engine", type=int, default=0)
    parser.add_argument("--out", default="smoke.json")
    args = parser.parse_args()

    payload = json.loads((ROOT / "index.json").read_text(encoding="utf-8"))
    
    # Filter by language
    extensions = [
        item
        for item in payload["extensions"]
        if str(item.get("language", "")).lower().startswith(args.lang)
    ]
    
    if args.only:
        wanted = {value.strip() for value in args.only.split(",") if value.strip()}
        extensions = [item for item in extensions if item["id"] in wanted]

    if args.samples_per_engine > 0 and not args.only:
        # Stratified sampling
        by_engine = defaultdict(list)
        for item in extensions:
            by_engine[item.get("engine", "custom")].append(item)
            
        sampled_extensions = []
        for eng, items in by_engine.items():
            if len(items) > args.samples_per_engine:
                # Randomize to not always hit the same ones
                sampled_extensions.extend(random.sample(items, args.samples_per_engine))
            else:
                sampled_extensions.extend(items)
        extensions = sampled_extensions
        random.shuffle(extensions) # global shuffle to mix requests

    gate = asyncio.Semaphore(args.concurrency)
    done = 0

    async def run(ext: dict) -> dict:
        nonlocal done
        extension_id = ext["id"]
        engine = ext.get("engine", "custom")
        async with gate:
            try:
                value = await asyncio.wait_for(
                    probe(extension_id, args.timeout, engine), timeout=args.timeout * 3,
                )
            except Exception as error:
                value = {"id": extension_id, "engine": engine, "steps": {"harness": {"status": "error", "error": brief(error)}}}
            done += 1
            print(f"[{done}/{len(extensions)}] {extension_id} ({engine})", flush=True)
            return value

    results = await asyncio.gather(*(run(ext) for ext in extensions))
    pathlib.Path(args.out).write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8",
    )
    print(f"\nEscrito {args.out} con {len(results)} resultados")
    
    # Aggregate and print
    agg = defaultdict(lambda: {"total": 0, "popular_items": 0, "popular_covers": 0})
    for r in results:
        e = r.get("engine", "custom")
        agg[e]["total"] += 1
        steps = r.get("steps", {})
        if "popular" in steps and steps["popular"].get("status") == "ok":
            agg[e]["popular_items"] += steps["popular"].get("items", 0)
            agg[e]["popular_covers"] += steps["popular"].get("cover", 0)
            
    print("\nResumen por motor (cover en popular):")
    for e, stat in sorted(agg.items(), key=lambda x: x[1]["total"], reverse=True):
        cov = stat["popular_covers"]
        tot = stat["popular_items"]
        if tot > 0:
            print(f" - {e} ({stat['total']} probados): cover {cov}/{tot} ({cov/tot:.0%})")
        else:
            print(f" - {e} ({stat['total']} probados): no items in popular")


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    # For Windows asyncio compatibility
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
