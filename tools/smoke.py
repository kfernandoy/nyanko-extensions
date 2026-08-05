"""Ejercita cada extension contra su sitio real y reporta que carril funciona.

Uso: python smoke.py [--lang es] [--only id,id] [--concurrency 8] [--timeout 90]

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
import traceback

import httpx

ROOT = pathlib.Path(__file__).resolve().parents[1]


class Fetcher:
    """Contrato SourceFetcher sobre httpx, respetando el rpm de la fuente."""

    def __init__(self, client: httpx.AsyncClient, headers: dict, rpm: int) -> None:
        self.client = client
        self.headers = headers
        self.interval = 60.0 / max(rpm, 1)
        self.last = 0.0
        self.lock = asyncio.Lock()
        self.count = 0

    async def request(self, method: str, url: str, **kwargs):
        async with self.lock:
            wait = self.interval - (time.monotonic() - self.last)
            if wait > 0:
                await asyncio.sleep(wait)
            self.last = time.monotonic()
        self.count += 1
        merged = {**self.headers, **dict(kwargs.pop("headers", None) or {})}
        return await self.client.request(method, url, headers=merged, **kwargs)


def load(extension_id: str):
    path = ROOT / "bundles" / f"{extension_id}.py"
    spec = importlib.util.spec_from_file_location(f"smoke_{extension_id}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SOURCE


def brief(error: BaseException) -> str:
    text = f"{type(error).__name__}: {error}".replace("\n", " ").strip()
    return text[:160]


async def probe(extension_id: str, timeout: float) -> dict:
    result = {"id": extension_id, "steps": {}, "requests": 0}
    try:
        factory = load(extension_id)
    except Exception as error:  # noqa: BLE001
        result["steps"]["load"] = brief(error)
        return result

    source = factory()
    limits = httpx.Limits(max_connections=4)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(25.0),
        follow_redirects=True,
        limits=limits,
        verify=False,
    ) as client:
        fetcher = Fetcher(
            client,
            dict(source.capabilities.headers),
            source.capabilities.requests_per_minute,
        )
        source = factory(fetcher)

        async def step(name: str, coro):
            try:
                value = await asyncio.wait_for(coro, timeout=timeout)
            except Exception as error:  # noqa: BLE001
                result["steps"][name] = brief(error)
                return None
            count = getattr(value, "items", None)
            if count is not None:
                result["steps"][name] = f"ok ({len(count)})" if count else "vacio"
            else:
                result["steps"][name] = "ok"
            return value

        popular = await step("popular", source.browse("popular", 1))
        latest = await step("latest", source.browse("latest", 1))
        await step("search", source.search("a", 1, None))

        items = []
        for candidate in (popular, latest):
            if candidate is not None and getattr(candidate, "items", None):
                items = candidate.items
                break
        if not items:
            result["requests"] = fetcher.count
            return result
        result["sample"] = items[0].source_id

        series = items[0]
        if hasattr(source, "details"):
            detailed = await step("details", source.details(series))
            series = detailed or series

        chapters = await step("chapters", source.chapters(series))
        if not chapters:
            result["requests"] = fetcher.count
            return result
        result["chapters"] = len(chapters)

        pages = await step("pages", source.pages(chapters[0]))
        if not pages:
            result["requests"] = fetcher.count
            return result
        result["pages"] = len(pages)

        content = await step("page_bytes", source.page_bytes(pages[0]))
        if content is not None:
            try:
                size = sum(len(chunk) for chunk in (content.chunks or []))
                result["bytes"] = size
                if size < 512:
                    result["steps"]["page_bytes"] = f"solo {size} bytes"
            except Exception as error:  # noqa: BLE001
                result["steps"]["page_bytes"] = brief(error)
    result["requests"] = fetcher.count
    return result


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lang", default="es")
    parser.add_argument("--only", default="")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--out", default="smoke.json")
    args = parser.parse_args()

    payload = json.loads((ROOT / "index.json").read_text(encoding="utf-8"))
    ids = [
        item["id"]
        for item in payload["extensions"]
        if str(item.get("language", "")).lower().startswith(args.lang)
    ]
    if args.only:
        wanted = {value.strip() for value in args.only.split(",") if value.strip()}
        ids = [value for value in ids if value in wanted]

    gate = asyncio.Semaphore(args.concurrency)
    done = 0

    async def run(extension_id: str) -> dict:
        nonlocal done
        async with gate:
            try:
                value = await asyncio.wait_for(
                    probe(extension_id, args.timeout), timeout=args.timeout * 3,
                )
            except Exception as error:  # noqa: BLE001
                value = {"id": extension_id, "steps": {"harness": brief(error)}}
            done += 1
            print(f"[{done}/{len(ids)}] {extension_id}", flush=True)
            return value

    results = await asyncio.gather(*(run(value) for value in ids))
    pathlib.Path(args.out).write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8",
    )
    print(f"\nEscrito {args.out} con {len(results)} resultados")


if __name__ == "__main__":
    import warnings

    warnings.filterwarnings("ignore")
    asyncio.run(main())
