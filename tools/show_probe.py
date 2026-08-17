"""Formatea el resultado del sondeo filtrando a lo estrictamente util."""
import json
from pathlib import Path

L = json.loads((Path(__file__).parent.parent / ".probe_urls.json").read_text(encoding="utf-8"))
for g in ["INALCANZABLE", "HTTP_525", "BLOQUEADO", "BLOQUEADO_CF", "SIN_URL"]:
    items = [r for r in L if r["veredicto"] == g]
    if not items: continue
    print(f"\n[{g}] {len(items)} fuentes")
    for r in items[:15]:
        print(f"  {r['id']:<26} {r.get('url','-'):<35} {r.get('error','')}")
    if len(items) > 15: print(f"  ... y {len(items)-15} mas")
