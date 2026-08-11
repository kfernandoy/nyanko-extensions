"""Valida todos los artefactos publicados por index.json."""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import struct
from pathlib import Path
from urllib.parse import urlsplit

from nyanko_api.sources.contract import SOURCE_API_VERSION, Source

try:
    from .sign_index import verify_index
except ImportError:  # Ejecucion directa: python tools/validate.py
    from sign_index import verify_index


def _assert_v4(source: object, extension_id: str) -> None:
    for method_name, parameters in {
        "search": ["query", "page", "filters"],
        "browse": ["kind", "page", "filters"],
        "chapters": ["series"],
        "pages": ["chapter"],
        "page_bytes": ["page"],
    }.items():
        method = getattr(source, method_name)
        assert inspect.iscoroutinefunction(method), f"{extension_id}: {method_name} no es async"
        actual = list(inspect.signature(method).parameters)
        assert actual == parameters, (
            f"{extension_id}: firma {method_name}{tuple(actual)}; "
            f"esperada {tuple(parameters)}"
        )

    for getter_name in ("get_filters", "get_preferences"):
        getter = getattr(source, getter_name)
        if inspect.iscoroutinefunction(getter):
            continue
        values = getter()
        assert isinstance(values, list), f"{extension_id}: {getter_name} no devuelve list"
        ids = [value.id for value in values]
        assert len(ids) == len(set(ids)), f"{extension_id}: IDs duplicados en {getter_name}"


def _local_file(repo: Path, url: str) -> Path:
    parts = [part for part in urlsplit(url).path.split("/") if part]
    artifact = next(
        (
            parts[index:]
            for index, part in enumerate(parts)
            if part in {"bundles", "icons"}
        ),
        parts,
    )
    target = repo.joinpath(*artifact).resolve()
    if repo.resolve() not in target.parents:
        raise AssertionError(f"URL fuera del repositorio: {url}")
    return target


def _load_source(path: Path, extension_id: str) -> object:
    spec = importlib.util.spec_from_file_location(f"validate_{extension_id}", path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"No se puede cargar {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = module.SOURCE()
    assert isinstance(source, Source), f"{extension_id}: no cumple Source"
    assert source.api_version == SOURCE_API_VERSION, f"{extension_id}: API incompatible"
    _assert_v4(source, extension_id)
    return source


def validate(repo: Path) -> int:
    payload = json.loads(verify_index(repo / "index.json"))
    seen: set[str] = set()
    for extension in payload["extensions"]:
        extension_id = extension["id"]
        assert extension_id not in seen, f"ID duplicado: {extension_id}"
        seen.add(extension_id)

        bundle = _local_file(repo, extension["bundle_url"])
        assert bundle.is_file(), f"Bundle ausente: {extension_id}"
        digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
        assert digest == extension["sha256"], f"SHA incorrecto: {extension_id}"
        source = _load_source(bundle, extension_id)
        assert source.name == extension_id, f"Nombre runtime distinto: {extension_id}"

        icon = _local_file(repo, extension["icon_url"])
        data = icon.read_bytes()
        assert data.startswith(b"\x89PNG\r\n\x1a\n"), f"Icono no PNG: {extension_id}"
        width, height = struct.unpack(">II", data[16:24])
        assert (width, height) == (192, 192), f"Icono {extension_id}: {width}x{height}"
    return len(seen)


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    print(f"Extensiones válidas: {validate(root)}")
