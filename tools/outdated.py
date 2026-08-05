"""Compara el versionCode de cada modulo Kotlin con el publicado en index.json.

Las fuentes sobre motor compartido se regeneran solas: `generate.py` relee el
Kotlin en cada pasada. Las `custom` no — sus entradas se copian del index.json
anterior, asi que su version, su URL y su logica quedan congeladas el dia que
se escribio el port. Este script es el unico aviso de que hay que re-portarlas.

    python tools/outdated.py [--source-root ...] [--lang es] [--all]
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

VERSION_CODE = re.compile(r"versionCode\s*=\s*(\d+)")
LANG_SUFFIX = re.compile(r"_(all|other|[a-z]{2}(?:_[a-z0-9]+)?)$")


def module_name(extension_id: str) -> str:
    return LANG_SUFFIX.sub("", extension_id)


def upstream_versions(source_root: Path) -> dict[str, int]:
    result: dict[str, int] = {}
    for build in source_root.glob("*/*/build.gradle.kts"):
        found = VERSION_CODE.search(build.read_text(encoding="utf-8"))
        if found:
            result[build.parent.name] = int(found.group(1))
    return result


def report(repo: Path, source_root: Path, language: str | None) -> int:
    upstream = upstream_versions(source_root)
    payload = json.loads((repo / "index.json").read_text(encoding="utf-8"))
    rows: list[tuple[str, str, int, int, str]] = []
    missing = 0
    for extension in payload["extensions"]:
        if language and not str(extension.get("language", "")).lower().startswith(language):
            continue
        newest = upstream.get(module_name(extension["id"]))
        if newest is None:
            missing += 1
            continue
        current = int(str(extension["version"]).split(".")[1])
        if newest != current:
            engine = str(extension.get("engine") or "?")
            rows.append((extension["id"], engine, current, newest, extension.get("language", "")))

    if not rows:
        print("Todas las extensiones estan a la version del Kotlin.")
        return 0

    # Las custom no se refrescan al regenerar: son las que exigen trabajo a mano.
    rows.sort(key=lambda row: (row[1] != "custom", row[0]))
    width = max(len(row[0]) for row in rows)
    print(f"{'extension':{width}}  motor          nuestra  upstream  accion")
    for extension_id, engine, current, newest, _ in rows:
        action = "RE-PORTAR a mano" if engine == "custom" else "regenerar"
        print(f"{extension_id:{width}}  {engine:<13}  v{current:<6}  v{newest:<7}  {action}")

    manual = sum(1 for row in rows if row[1] == "custom")
    print(
        f"\n{len(rows)} desfasadas: {manual} manuales (re-portar) y "
        f"{len(rows) - manual} generadas (basta `python tools/generate.py`).",
    )
    if missing:
        print(f"{missing} extensiones sin modulo Kotlin localizable; se ignoran.")
    return len(rows)


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, default=repo.parent / "extensions-source-main" / "src")
    parser.add_argument("--lang", default=None, help="prefijo de idioma, p.ej. es")
    parser.add_argument("--all", action="store_true", help="atajo para no filtrar por idioma")
    args = parser.parse_args()
    report(repo, args.source_root.resolve(), None if args.all else args.lang)


if __name__ == "__main__":
    main()
