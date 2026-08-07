import json
import os

index_path = "index.json"
results_dir = "../Nyanko/.planning/extension-validation/results"

with open(index_path, "r", encoding="utf-8") as f:
    index_data = json.load(f)

engine_map = {ext["id"]: ext.get("engine", "") for ext in index_data.get("extensions", [])}

updated_count = 0
missed_count = 0

for ext_id, engine in engine_map.items():
    res_path = os.path.join(results_dir, f"{ext_id}.json")
    if os.path.exists(res_path):
        with open(res_path, "r", encoding="utf-8") as f:
            res_data = json.load(f)
        
        changed = False
        if res_data.get("engine") != engine:
            res_data["engine"] = engine
            changed = True
        
        if not res_data.get("kotlin_commit"):
            res_data["kotlin_commit"] = "snapshot-2026-08-05"
            changed = True
            
        if changed:
            with open(res_path, "w", encoding="utf-8") as f:
                json.dump(res_data, f, indent=2)
            updated_count += 1
    else:
        missed_count += 1

print(f"Updated {updated_count} result JSONs.")
print(f"Missed {missed_count} due to missing JSON file.")
