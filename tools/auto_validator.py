import json
import asyncio
import argparse
from pathlib import Path
from datetime import datetime, timezone
import sys
import random

sys.path.insert(0, str(Path(__file__).resolve().parent))
from smoke import probe, brief, Fetcher

RESULTS_DIR = Path("../Nyanko/.planning/extension-validation/results")

# Estado que se asigna segun POR QUE fallo. Distinguirlo evita mandar a alguien a portar
# codigo cuando el problema es que el sitio esta caido o exige resolver un captcha.
ESTADO_POR_CAUSA = {
    "blocked": "BLOCKED_NETWORK",      # 403/Cloudflare: haria falta WebView, no mas codigo
    "offline": "BLOCKED_NETWORK",      # DNS/conexion: el sitio no responde
    "port": "IMPLEMENTATION_REQUIRED",  # el sitio responde pero el parseo no saca datos
}


def clasificar_fallo(reasons: list[str]) -> str:
    """Distingue un fallo del PORT de un bloqueo externo.

    Verificado en vivo (2026-08-08). Un 403 NO significa "imposible": significa que falta
    implementar el handshake que el proveedor exige.

      - DDoS-Guard: se resuelve pidiendo su cookie `__ddg2_`. Ya implementado en
        `smoke.resolver_ddos_guard`, portado del DDosGuardInterceptor de Mihon. akuma_es
        pasó de 403 a VERIFIED con eso.
      - Cloudflare (`cf-mitigated: challenge`): el reto es JavaScript. Mihon lo resuelve
        con el WebView de Android (core/.../WebView.kt), no con codigo del bundle, asi que
        un harness en Python puro NO puede pasarlo. Eso es lo que marca `blocked`.

    `offline` es el dominio que ya ni resuelve.
    """
    texto = " ".join(reasons)
    if "403" in texto or "Forbidden" in texto or "cf-mitigated" in texto:
        return "blocked"
    if (
        "ConnectError" in texto
        or "getaddrinfo" in texto
        or "ConnectTimeout" in texto
        or "ReadTimeout" in texto
    ):
        return "offline"
    return "port"

async def run_validation(
    batch_size: int,
    force_engine: str = "",
    only_ids: set[str] | None = None,
    lang_suffix: str = "",
):
    candidates = []

    for json_file in RESULTS_DIR.glob("*.json"):
        data = json.loads(json_file.read_text("utf-8"))
        source_id = data.get("source_id", json_file.stem)

        # Seleccion explicita (--ids / --lang): manda sobre los filtros de estado. Sirve para
        # probar EXACTAMENTE lo que el usuario tiene instalado, incluso si ya esta VERIFIED o
        # si tiene diferencias pendientes: aqui la pregunta no es "que falta portar" sino
        # "cual de las mias trae datos ahora mismo".
        if only_ids is not None:
            if source_id in only_ids:
                candidates.append(json_file)
            continue
        if lang_suffix:
            if source_id.endswith(lang_suffix):
                candidates.append(json_file)
            continue

        # We target READY_REVIEW or PENDING with 0 differences
        # BLOCKED_NETWORK tambien se salta en el muestreo del backlog: reintentar un sitio
        # con Cloudflare gasta la tanda sin aportar informacion nueva. Se puede reprobar
        # explicitamente con --ids cuando se quiera comprobar si el bloqueo sigue.
        if data.get("status") in (
            "VERIFIED", "RETIRED", "BLOCKED_MAPPING", "BLOCKED_CONTRACT", "BLOCKED_NETWORK",
        ):
            continue
        if data.get("differences"):
            continue

        if force_engine and data.get("engine") != force_engine:
            continue

        candidates.append(json_file)

    if not candidates:
        print("No candidates found with 0 differences and READY_REVIEW/PENDING status.")
        return

    # Con seleccion explicita se respeta el orden alfabetico y se prueban TODAS: barajar y
    # recortar solo tiene sentido para el muestreo del backlog.
    if only_ids is None and not lang_suffix:
        random.shuffle(candidates)
        selected = candidates[:batch_size]
    else:
        selected = sorted(candidates, key=lambda p: p.stem)[:batch_size]
    print(f"Selected {len(selected)} candidates for automated VERIFIED promotion...")
    
    success_count = 0
    
    for json_file in selected:
        data = json.loads(json_file.read_text("utf-8"))
        ext_id = data["source_id"]
        engine = data.get("engine", "custom")
        
        print(f"\nProbing {ext_id} ({engine})...")
        
        try:
            val_result = await asyncio.wait_for(probe(ext_id, 30.0, engine), timeout=120.0)
        except Exception as e:
            val_result = {"id": ext_id, "steps": {"harness": {"status": "error", "error": brief(e)}}}
            
        steps = val_result.get("steps", {})
        is_success = True
        reasons = []
        
        step_pop = steps.get("popular", {})
        if step_pop.get("status") != "ok" or step_pop.get("items", 0) == 0:
            is_success = False
            reasons.append(f"popular failed or empty: {step_pop}")
            
        step_chap = steps.get("chapters", {})
        if step_chap.get("status") != "ok" or step_chap.get("items", 0) == 0:
            is_success = False
            reasons.append(f"chapters failed or empty: {step_chap}")
            
        step_page = steps.get("pages", {})
        if step_page.get("status") != "ok" or step_page.get("items", 0) == 0:
            is_success = False
            reasons.append(f"pages failed or empty: {step_page}")
            
        step_bytes = steps.get("page_bytes", {})
        if step_bytes.get("status") != "ok":
            is_success = False
            reasons.append(f"page_bytes failed: {step_bytes}")
            
        if step_pop.get("status") == "ok" and step_pop.get("items", 0) > 0:
            if step_pop.get("cover", 0) == 0:
                is_success = False
                reasons.append("popular returned 0 covers")
                
        timestamp = datetime.now(timezone.utc).isoformat()
        live_check = {
            "date": timestamp,
            "success": is_success,
            "requests_used": val_result.get("requests", 0),
            "summary": json.dumps(val_result.get("steps", {}))
        }
        
        if "live_checks" not in data:
            data["live_checks"] = []
        data["live_checks"].append(live_check)
        
        if is_success:
            print(f" -> SUCCESS! All functional gates passed.")
            feats = data.get("features", {})
            if feats.get("popular") == "PENDING": feats["popular"] = "PASS"
            if feats.get("latest") == "PENDING" and steps.get("latest", {}).get("status") == "ok": feats["latest"] = "PASS"
            if feats.get("search") == "PENDING" and steps.get("search", {}).get("status") == "ok": feats["search"] = "PASS"
            if feats.get("chapters") == "PENDING": feats["chapters"] = "PASS"
            if feats.get("pages") == "PENDING": feats["pages"] = "PASS"
            if feats.get("page_bytes") == "PENDING": feats["page_bytes"] = "PASS"
            if feats.get("cover") == "PENDING": feats["cover"] = "PASS"
            if feats.get("details") == "PENDING" and steps.get("details", {}).get("status") == "ok": feats["details"] = "PASS"
            
            data["status"] = "VERIFIED"
            data["reviewer"] = "auto_validator_bot"
            data["reviewed_at"] = timestamp
            # Una extension recuperada arrastraba el `failure_cause` de la ejecucion
            # anterior, asi que quedaba VERIFIED y `failure_cause: port` a la vez.
            # Cualquier informe que filtre por ese campo la sigue contando como rota.
            data.pop("failure_cause", None)
            if data.get("blockers"):
                data["blockers"] = [
                    b for b in data["blockers"]
                    if not str(b).startswith("Auto-validation failed [")
                ]
            success_count += 1
        else:
            causa = clasificar_fallo(reasons)
            print(f" -> FAIL [{causa}]! Reasons: {reasons}")
            # No todo fallo es culpa del port. Marcar como IMPLEMENTATION_REQUIRED un sitio
            # caido o detras de Cloudflare manda a alguien a "arreglar" codigo que ya esta
            # bien: el bloqueo es externo y ninguna linea de Python lo resuelve.
            data["status"] = ESTADO_POR_CAUSA[causa]
            data["failure_cause"] = causa
            if "blockers" not in data:
                data["blockers"] = []
            data["blockers"].append(f"Auto-validation failed [{causa}]: {reasons[0]}")
            
        json_file.write_text(json.dumps(data, indent=2), "utf-8")
        await asyncio.sleep(1.0)
        
    print(f"\n=============================")
    print(f"Batch completed! {success_count}/{batch_size} auto-promoted to VERIFIED.")
    
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", type=int, default=5, help="Number of extensions to validate")
    parser.add_argument("--engine", type=str, default="", help="Force specific engine")
    parser.add_argument(
        "--ids",
        type=str,
        default="",
        help="Lista separada por comas de source_id concretos (p.ej. las instaladas)",
    )
    parser.add_argument(
        "--lang",
        type=str,
        default="",
        help="Sufijo de idioma a probar, p.ej. _es o _es_419",
    )
    args = parser.parse_args()

    ids = {i.strip() for i in args.ids.split(",") if i.strip()} or None

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_validation(args.batch, args.engine, ids, args.lang))
