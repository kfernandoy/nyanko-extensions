import json
from pathlib import Path
from collections import Counter

results_dir = Path("../Nyanko/.planning/extension-validation/results")

total_pending = 0
total_impl_req = 0
diffs_count = 0
engine_counts = Counter()
engine_issues = Counter()

for json_file in results_dir.glob("*.json"):
    data = json.loads(json_file.read_text("utf-8"))
    
    # Measure pending
    for k, v in data.get("features", {}).items():
        if v == "PENDING":
            total_pending += 1
        elif v == "IMPLEMENTATION_REQUIRED":
            total_impl_req += 1
            
    diffs = data.get("differences", [])
    diffs_count += len(diffs)
    
    engine = data.get("engine")
    if engine:
        engine_counts[engine] += 1
        engine_issues[engine] += len(diffs)
        
print(f"Celdas PENDING: {total_pending}")
print(f"Celdas IMPLEMENTATION_REQUIRED: {total_impl_req}")
print(f"Total differences: {diffs_count}")
print("Top motores por diferencias encontradas:")
for eng, count in engine_issues.most_common(10):
    print(f" - {eng}: {count} diferencias en {engine_counts[eng]} extensiones")
