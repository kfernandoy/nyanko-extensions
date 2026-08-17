"""Reproduce la ruta real del generador para Manta.

La ruta `custom` (generate.py:3783-3797) SI expande `lang = it` con
`_expand_source`. Este script comprueba en que punto concreto se pierde el
metadato para Manta.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = Path(
    r"E:\2023-09-04\anitracker\extensions-source-main\src\all\manta\build.gradle.kts"
)

spec = importlib.util.spec_from_file_location("gen", REPO / "tools" / "generate.py")
gen = importlib.util.module_from_spec(spec)
sys.modules["gen"] = gen
spec.loader.exec_module(gen)


def main() -> int:
    build = BUILD.read_text(encoding="utf-8")

    engine = gen._match(r'theme\s*=\s*"([^"]+)"', build) or "custom"
    print(f"engine detectado          : {engine}")

    bloques = gen._source_blocks(build) or [""]
    print(f"bloques source encontrados: {len(bloques)}")
    for i, b in enumerate(bloques):
        print(f"   [{i}] {b.strip()[:120]!r}")

    crudo = gen._match(r"listOf\(([\s\S]*?)\)\.forEach", build)
    print(f"\nlistOf(...) crudo         : {crudo!r}")
    idiomas = re.findall(r'"([^"]+)"', crudo or "")
    print(f"idiomas extraidos         : {idiomas}")

    for i, b in enumerate(bloques):
        tiene = bool(re.search(r"\blang\s*=\s*it\b", b))
        print(f"\nbloque[{i}] tiene 'lang = it': {tiene}")
        if tiene and idiomas:
            for lang in idiomas:
                exp = gen._expand_source(b, lang)
                bu = gen._match(r'baseUrl\s*=\s*"(https?://[^"]+)"', exp)
                lg = gen._match(r'lang\s*=\s*"([^"]+)"', exp)
                nm = gen._match(r'\bname\s*=\s*"([^"]+)"', exp)
                print(f"   {lang}: base_url={bu!r} language={lg!r} name={nm!r}")
                print(f"       all() -> {all((bu, lg, nm))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
