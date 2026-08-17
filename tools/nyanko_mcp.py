import asyncio
import json
import os
from pathlib import Path

import httpx
from fastmcp import FastMCP

REPO = Path(r"E:\2023-09-04\anitracker\nyanko-extensions")
APP_DATA = Path(os.environ["APPDATA"]) / "app.nyanko.desktop"
API_URL = os.environ.get("NYANKO_API_URL", "http://127.0.0.1:8765")
UI_BRIDGE = REPO / "tools" / "nyanko_ui_bridge.js"

mcp = FastMCP("Nyanko Tests")


def _instance_headers() -> dict[str, str]:
    token = (APP_DATA / "instance_token").read_text(encoding="utf-8").strip()
    return {"X-Nyanko-Instance": token}


async def _show_source(ext_id: str) -> dict[str, object]:
    process = await asyncio.create_subprocess_exec(
        "node",
        str(UI_BRIDGE),
        ext_id,
        cwd=str(REPO),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode:
        raise RuntimeError(stderr.decode("utf-8", errors="replace").strip())
    return json.loads(stdout)


async def _api_call(ext_id: str, method: str, args: dict[str, object]) -> object:
    params: dict[str, object] = {"source": ext_id}
    endpoint = method

    if method == "browse":
        params.update(kind=args.get("kind", "popular"), page=args.get("page", 1))
    elif method == "search":
        params.update(query=args.get("query", ""), page=args.get("page", 1))
    elif method in {"details", "series"}:
        endpoint = "series"
        params["series_id"] = args.get("series_id") or args.get("source_id")
    elif method == "chapters":
        params["series_id"] = args.get("series_id") or args.get("source_id")
    elif method == "pages":
        params["chapter_id"] = args.get("chapter_id") or args.get("source_id")
    else:
        raise ValueError(f"Metodo no soportado por la API viva: {method}")

    if "filters" in args:
        params["filters"] = json.dumps(args["filters"], ensure_ascii=False)

    async with httpx.AsyncClient(base_url=API_URL, headers=_instance_headers(), timeout=90) as client:
        response = await client.get(f"/api/manga/{endpoint}", params=params)
        response.raise_for_status()
        return response.json()


@mcp.tool()
async def nyanko_call(ext_id: str, method: str, kwargs: str = "{}") -> str:
    """Muestra una fuente en Nyanko y la valida mediante el backend vivo.

    Args:
       ext_id: ID instalado de la extension, por ejemplo "manta_es".
       method: Metodo HTTP: browse, search, details, chapters o pages.
       kwargs: Argumentos como JSON, por ejemplo '{"kind": "popular"}'.
    """
    try:
        args = json.loads(kwargs)
        if not isinstance(args, dict):
            raise ValueError("kwargs debe ser un objeto JSON")
    except (json.JSONDecodeError, ValueError) as error:
        return f"Error: kwargs no es un objeto JSON valido: {error}"

    try:
        ui = await _show_source(ext_id)
        result = await _api_call(ext_id, method, args)
        return json.dumps({"ui": ui, "result": result}, ensure_ascii=False, indent=2)
    except Exception:
        import traceback

        return f"Error ejecutando {ext_id}.{method}:\n{traceback.format_exc()}"


if __name__ == "__main__":
    mcp.run()
