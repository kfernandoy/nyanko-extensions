"""Extrae la lista de validacion humana en JSON manejable.

Separa lo ya validado por el usuario (hasta manta_es incluido) de lo pendiente
(despues de manta_es), y clasifica cada entrada como OK / PENDIENTE / CON ERROR.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ARCHIVO = REPO / "validacion_humana2.txt"

# Linea tipo: "- [X] id_es (Nombre) Error: ..." 
ENTRADA = re.compile(r"^- \[( |X|x)\] (\S+?) \(([^)]*)\)\s*(.*)$")
MOTOR = re.compile(r"^#### Motor:\s*(\S+)")

CORTE = "manta_es"  # ultima fuente que valido el usuario


def main() -> int:
    motor = "?"
    filas = []
    visto_corte = False

    for numero, linea in enumerate(
        ARCHIVO.read_text(encoding="utf-8").splitlines(), start=1
    ):
        m_motor = MOTOR.match(linea.strip())
        if m_motor:
            motor = m_motor.group(1)
            continue
        m = ENTRADA.match(linea.strip())
        if not m:
            continue
        marca, ext_id, nombre, nota = m.groups()
        ok = marca.lower() == "x"
        filas.append(
            {
                "linea": numero,
                "id": ext_id,
                "nombre": nombre,
                "motor": motor,
                "ok": ok,
                "nota": nota.strip(),
                "ya_revisado": not visto_corte,
            }
        )
        if ext_id == CORTE:
            visto_corte = True

    salida = REPO / ".validacion.json"
    salida.write_text(json.dumps(filas, ensure_ascii=False, indent=1), encoding="utf-8")

    revisados = [f for f in filas if f["ya_revisado"]]
    pendientes = [f for f in filas if not f["ya_revisado"]]
    con_error = [f for f in revisados if not f["ok"] and f["nota"]]

    print(f"entradas totales      : {len(filas)}")
    print(f"ya revisadas (<=manta): {len(revisados)}")
    print(f"   marcadas OK        : {sum(1 for f in revisados if f['ok'])}")
    print(f"   con error anotado  : {len(con_error)}")
    print(f"pendientes (>manta)   : {len(pendientes)}")
    print(f"\nescrito {salida.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
