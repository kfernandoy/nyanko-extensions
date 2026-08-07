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

async def run_validation(batch_size: int, force_engine: str = ""):
    candidates = []
    
    for json_file in RESULTS_DIR.glob("*.json"):
        data = json.loads(json_file.read_text("utf-8"))
        # We target READY_REVIEW or PENDING with 0 differences
        if data.get("status") in ("VERIFIED", "RETIRED", "BLOCKED_MAPPING", "BLOCKED_CONTRACT"):
            continue
        if data.get("differences"):
            continue
            
        if force_engine and data.get("engine") != force_engine:
            continue
            
        candidates.append(json_file)
        
    if not candidates:
        print("No candidates found with 0 differences and READY_REVIEW/PENDING status.")
        return
        
    random.shuffle(candidates)
    selected = candidates[:batch_size]
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
            success_count += 1
        else:
            print(f" -> FAIL! Reasons: {reasons}")
            data["status"] = "IMPLEMENTATION_REQUIRED"
            if "blockers" not in data:
                data["blockers"] = []
            data["blockers"].append(f"Auto-validation failed: {reasons[0]}")
            
        json_file.write_text(json.dumps(data, indent=2), "utf-8")
        await asyncio.sleep(1.0)
        
    print(f"\n=============================")
    print(f"Batch completed! {success_count}/{batch_size} auto-promoted to VERIFIED.")
    
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", type=int, default=5, help="Number of extensions to validate")
    parser.add_argument("--engine", type=str, default="", help="Force specific engine")
    args = parser.parse_args()
    
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_validation(args.batch, args.engine))
