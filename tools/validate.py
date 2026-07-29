"""Valida todos los artefactos publicados por index.json."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import struct
from pathlib import Path
from urllib.parse import urlsplit

from nyanko_api.sources.contract import SOURCE_API_VERSION, Source


def _local_file(repo: Path, url: str) -> Path:
    path = urlsplit(url).path.lstrip("/")
    target = (repo / path).resolve()
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
    return source


def validate(repo: Path) -> int:
    payload = json.loads((repo / "index.json").read_text(encoding="utf-8"))
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
