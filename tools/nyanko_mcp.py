# mcp server
import asyncio
import json
import logging
import sys
import types
from inspect import iscoroutine
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

# Config
REPO = Path(r"E:\2023-09-04\anitracker\nyanko-extensions")
BACKEND = Path(r"E:\2023-09-04\anitracker\Nyanko\apps\backend")

mcp = FastMCP("Nyanko Tests")


def _load_source(ext_id: str) -> Any:
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    
    path = REPO / "bundles" / f"{ext_id}.py"
    if not path.exists():
        raise FileNotFoundError(f"Bundle no encontrado: {path}")

    mod = types.ModuleType(ext_id)
    mod.__dict__["__file__"] = str(path)
    
    try:
        exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), mod.__dict__)
    except Exception as e:
        raise RuntimeError(f"Error parseando/ejecutando {ext_id}.py: {e}") from e
    
    src_cls = mod.__dict__.get("SOURCE")
    if not src_cls:
        raise RuntimeError(f"No se encontro SOURCE en {ext_id}.py")
        
    return src_cls()


@mcp.tool()
async def nyanko_call(ext_id: str, method: str, kwargs: str = "{}") -> str:
    """Invoca un metodo en una fuente (ej. search, browse, details, chapters, pages).
    Args:
       ext_id: ID de la extension (ej. "manta_es")
       method: Nombre del metodo (ej. "search")
       kwargs: Argumentos en JSON comun (ej. '{"query": "magia"}')
    """
    try:
        args = json.loads(kwargs)
    except json.JSONDecodeError:
        return f"Error: kwargs no es un JSON valido: {kwargs}"
        
    try:
        source = _load_source(ext_id)
    except Exception as e:
        return f"Error instanciando {ext_id}: {e}"
        
    func = getattr(source, method, None)
    if not func:
        return f"Error: La fuente {ext_id} no tiene el metodo {method}"
        
    # Casos especiales de serializacion en argumentos que esperan instacias como SourceSeries
    # Si requiere una clase de Nyanko, intentamos pasar el ID directamente (los motores suelen aceptar string o model)
    from nyanko_api.sources.contract import SourceSeries, SourceChapter

    # Mapeo manual simple para convertir dicts (si mandan JSON) en modelos
    mapped_args = {}
    for k, v in args.items():
        if isinstance(v, dict) and "source_id" in v:
            # es probable que intenten pasar una serie y el metodo soporta objeto o string
            mapped_args[k] = SourceSeries(**v) if "title" in v else SourceChapter(**v)
        else:
            mapped_args[k] = v

    try:
        res = func(**mapped_args)
        if iscoroutine(res):
            res = await res
            
        # Intentamos volcar el resultado como JSON para que sea legible
        try:
            # Si el resultado es lista de modelos Pydantic
            if isinstance(res, list) and len(res) > 0 and hasattr(res[0], "model_dump"):
                return json.dumps([x.model_dump() for x in res], indent=2)
            elif hasattr(res, "model_dump"):
                return json.dumps(res.model_dump(), indent=2)
            else:
                return json.dumps(res, indent=2)
        except Exception:
            return f"Resultado ({type(res).__name__}): {res}"
    except Exception as e:
        import traceback
        return f"Error ejecutando {ext_id}.{method}:\n{traceback.format_exc()}"


if __name__ == "__main__":
    mcp.run(transport='stdio')
