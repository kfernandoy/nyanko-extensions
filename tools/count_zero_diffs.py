import json
from pathlib import Path

results_dir = Path("../Nyanko/.planning/extension-validation/results")
zero_diffs = []
for json_file in results_dir.glob("*.json"):
    data = json.loads(json_file.read_text("utf-8"))
    if not data.get("differences"):
        zero_diffs.append(data.get("engine", "unknown"))

from collections import Counter
print(f"Total with 0 diffs: {len(zero_diffs)}")
print(Counter(zero_diffs).most_common())
