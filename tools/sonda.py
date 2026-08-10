"""Sonda manual: pide una URL con el UA del harness y resume la respuesta.

Uso: python tools/sonda.py URL [--post] [--data k=v ...] [--grep patron] [--dump n]
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys

import httpx

UA = (
    "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Mobile Safari/537.36"
)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--post", action="store_true")
    parser.add_argument("--data", nargs="*", default=[])
    parser.add_argument("--header", nargs="*", default=[])
    parser.add_argument("--grep", default="")
    parser.add_argument("--dump", type=int, default=0)
    args = parser.parse_args()

    datos = dict(par.split("=", 1) for par in args.data)
    cabeceras = {"User-Agent": UA}
    cabeceras.update(dict(par.split("=", 1) for par in args.header))
    async with httpx.AsyncClient(
        timeout=30.0, follow_redirects=True, verify=False, headers=cabeceras,
    ) as client:
        if args.post:
            respuesta = await client.post(args.url, data=datos)
        else:
            respuesta = await client.get(args.url)

    print(f"{respuesta.status_code} {respuesta.url} len={len(respuesta.text)}")
    print({k: v for k, v in respuesta.headers.items() if k.lower() in {"content-type", "server", "location"}})
    if args.grep:
        for encontrado in re.findall(args.grep, respuesta.text)[:40]:
            print("  ", encontrado if isinstance(encontrado, str) else encontrado)
    if args.dump:
        sys.stdout.write(respuesta.text[: args.dump])


if __name__ == "__main__":
    import warnings

    warnings.filterwarnings("ignore")
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
