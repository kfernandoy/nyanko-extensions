"""Firma y verifica index.json con Ed25519 detached."""

from __future__ import annotations

import argparse
import base64
import binascii
import os
import stat
import sys
import tempfile
from pathlib import Path

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )
except ImportError as exc:  # pragma: no cover - depende del entorno de ejecucion
    raise SystemExit(
        "Falta la dependencia 'cryptography'; instalala con: pip install cryptography"
    ) from exc


SIGNING_KEY_ENV = "NYANKO_REPO_SIGNING_KEY"


class IndexSignatureError(ValueError):
    """La firma del indice o alguno de sus archivos no es valido."""


def _sidecar_paths(index_path: Path) -> tuple[Path, Path]:
    return (
        index_path.with_name(f"{index_path.name}.pub"),
        index_path.with_name(f"{index_path.name}.sig"),
    )


def _canonical_base64(raw: bytes) -> bytes:
    return base64.b64encode(raw) + b"\n"


def _decode_sidecar(path: Path, expected_length: int, description: str) -> bytes:
    try:
        encoded = path.read_bytes()
    except OSError as exc:
        raise IndexSignatureError(f"No se puede leer {description} {path}: {exc}") from exc
    if not encoded.endswith(b"\n") or encoded.endswith(b"\n\n"):
        raise IndexSignatureError(
            f"Formato invalido en {path}: se esperaba base64 estandar y un solo newline final"
        )
    try:
        raw = base64.b64decode(encoded[:-1], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise IndexSignatureError(f"Base64 invalido en {path}") from exc
    if len(raw) != expected_length or encoded != _canonical_base64(raw):
        raise IndexSignatureError(
            f"Formato invalido en {path}: {description} debe contener "
            f"{expected_length} bytes en base64 estandar y un newline"
        )
    return raw


def _atomic_write(path: Path, data: bytes) -> None:
    temporary: str | None = None
    try:
        descriptor, temporary = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
        temporary = None
    except OSError as exc:
        raise IndexSignatureError(f"No se puede escribir {path}: {exc}") from exc
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def resolve_private_key(private_key: str | Path | None = None) -> Path:
    value = private_key or os.environ.get(SIGNING_KEY_ENV)
    if not value:
        raise IndexSignatureError(
            f"Falta la clave privada: usa --private-key o define {SIGNING_KEY_ENV}"
        )
    return Path(value).expanduser()


def _load_private_key(path: Path) -> Ed25519PrivateKey:
    try:
        pem = path.read_bytes()
    except OSError as exc:
        raise IndexSignatureError(f"No se puede leer la clave privada {path}: {exc}") from exc
    if os.name == "posix" and path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise IndexSignatureError(
            f"Permisos inseguros en la clave privada {path}; aplica chmod 600"
        )
    if not pem.startswith(b"-----BEGIN PRIVATE KEY-----"):
        raise IndexSignatureError(
            f"La clave privada {path} debe ser Ed25519 en PEM PKCS8 sin cifrar"
        )
    try:
        key = serialization.load_pem_private_key(pem, password=None)
    except (TypeError, ValueError) as exc:
        raise IndexSignatureError(f"Clave privada PEM PKCS8 invalida: {path}") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise IndexSignatureError(f"La clave privada no es Ed25519: {path}")
    return key


def sign_index(index_path: Path, private_key_path: Path) -> tuple[Path, Path]:
    """Firma los bytes exactos del indice y reemplaza sus sidecars atomicamente."""
    try:
        payload = index_path.read_bytes()
    except OSError as exc:
        raise IndexSignatureError(f"No se puede leer el indice {index_path}: {exc}") from exc
    key = _load_private_key(private_key_path)
    public_raw = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    signature = key.sign(payload)
    public_path, signature_path = _sidecar_paths(index_path)
    _atomic_write(public_path, _canonical_base64(public_raw))
    _atomic_write(signature_path, _canonical_base64(signature))
    return public_path, signature_path


def verify_index(index_path: Path) -> bytes:
    """Verifica los sidecars y devuelve exactamente los bytes autenticados."""
    try:
        payload = index_path.read_bytes()
    except OSError as exc:
        raise IndexSignatureError(f"No se puede leer el indice {index_path}: {exc}") from exc
    public_path, signature_path = _sidecar_paths(index_path)
    public_raw = _decode_sidecar(public_path, 32, "clave publica Ed25519")
    signature = _decode_sidecar(signature_path, 64, "firma Ed25519")
    try:
        Ed25519PublicKey.from_public_bytes(public_raw).verify(signature, payload)
    except (InvalidSignature, ValueError) as exc:
        raise IndexSignatureError(
            f"Firma Ed25519 invalida para {index_path}; el indice o sus sidecars fueron alterados"
        ) from exc
    return payload


def _parser(default_index: Path) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    sign = commands.add_parser("sign", help="firma index.json y escribe .pub y .sig")
    sign.add_argument("index", nargs="?", type=Path, default=default_index)
    sign.add_argument("--private-key", type=Path)

    verify = commands.add_parser("verify", help="verifica index.json contra .pub y .sig")
    verify.add_argument("index", nargs="?", type=Path, default=default_index)
    return parser


def main(argv: list[str] | None = None) -> int:
    default_index = Path(__file__).resolve().parents[1] / "index.json"
    args = _parser(default_index).parse_args(argv)
    try:
        if args.command == "sign":
            public_path, signature_path = sign_index(
                args.index,
                resolve_private_key(args.private_key),
            )
            print(f"Indice firmado: {args.index}")
            print(f"Clave publica: {public_path}")
            print(f"Firma: {signature_path}")
        else:
            verify_index(args.index)
            print(f"Firma valida: {args.index}")
    except IndexSignatureError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
